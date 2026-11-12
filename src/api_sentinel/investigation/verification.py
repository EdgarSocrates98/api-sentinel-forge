"""Verification plans — falsifiable checks bound to authorized tools.

A `VerificationPlan` is a typed list of steps: each names the check, the
tool/specialist that must run it (`re_detect`, `atlas_resolve`,
`doctor_handoff`), the `expected_signal` that would support the
hypothesis and the `falsification` that would refute it. Plans are
executed through the existing ports only — the Sentinel never fabricates
a check.

Verdict semantics (deterministic, honest):

- `REFUTED` when any step produced falsification evidence;
- `VERIFIED` when at least one step produced independent supporting
  evidence and none falsified;
- `INCONCLUSIVE` otherwise — unavailable/refused tools are named states
  on their receipts, never silently counted.

Independent post-fix verification: the `re_detect` step re-runs the
incident's grouping rule over a post-change window; the rule must *not*
re-fire for a `VERIFIED`-supporting receipt — resolution is never
self-declared (docs/09).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import Field

from api_sentinel.application.ports import UnitOfWork
from api_sentinel.detection import (
    DetectionContext,
    DetectionRequest,
    DetectorRegistry,
    default_rules,
    detection_request_id,
    evaluate_rule,
    summarize_baselines,
)
from api_sentinel.domain.errors import NotFoundError
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.incidents import group_key_for
from api_sentinel.incidents.journal import EventKind, IncidentEvent, make_event
from api_sentinel.incidents.models import Incident, VerificationRecord
from api_sentinel.integrations.atlas.port import StructuralContextPort
from api_sentinel.integrations.doctor.adapter import DoctorACL
from api_sentinel.metrics.windows import TimeWindow

# ---------------------------------------------------------------------------
# Typed plan + receipts


class VerificationStep(FrozenModel):
    """One falsifiable check bound to an authorized tool.

    `kind` selects the executing port; `check` states what is examined,
    `expected_signal` what would support the hypothesis, `falsification`
    what would refute it, `scope` the authorized scope of the check.
    """

    step_id: str
    kind: str  # re_detect | atlas_resolve | doctor_handoff
    check: str
    expected_signal: str
    falsification: str
    scope: str = ""
    params: dict[str, str] = Field(default_factory=dict)


class VerificationPlan(FrozenModel):
    """A bounded set of checks for one hypothesis — falsifiable by design."""

    verification_id: str
    incident_id: str
    hypothesis_id: str
    created_at: datetime
    steps: list[VerificationStep] = Field(default_factory=list)


class StepReceipt(FrozenModel):
    """One executed step — named status + evidence refs, never payloads."""

    step_id: str
    kind: str
    status: str  # supporting | falsified | inconclusive | unavailable | refused | failed
    detail: str = ""
    evidence_refs: list[str] = Field(default_factory=list)

    def as_record(self) -> dict[str, str]:
        return {
            "step_id": self.step_id,
            "kind": self.kind,
            "status": self.status,
            "detail": self.detail,
            "evidence_refs": ",".join(self.evidence_refs),
        }


VERDICT_VERIFIED = "VERIFIED"
VERDICT_REFUTED = "REFUTED"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"


class VerificationResult(FrozenModel):
    """The executed plan — verdict + receipts, persisted as a record."""

    verification_id: str
    incident_id: str
    hypothesis_id: str
    verdict: str  # VERIFIED | REFUTED | INCONCLUSIVE
    receipts: list[StepReceipt] = Field(default_factory=list)
    executed_at: datetime
    detail: str = ""

    def to_record(self) -> VerificationRecord:
        return VerificationRecord(
            verification_id=self.verification_id,
            hypothesis_id=self.hypothesis_id,
            verdict=self.verdict,
            receipts=[r.as_record() for r in self.receipts],
            detail=self.detail,
            executed_at=self.executed_at,
        )


def verification_id_for(*, incident_id: str, hypothesis_id: str, at: datetime) -> str:
    canonical = f"{incident_id}|{hypothesis_id}|{at.isoformat()}"
    return "ver_" + hashlib.sha256(canonical.encode()).hexdigest()[:24]


def default_verification_plan(
    *,
    incident: Incident,
    hypothesis_id: str,
    at: datetime,
    window_start: datetime,
    window_end: datetime,
    structural_available: bool,
    include_doctor: bool,
) -> VerificationPlan:
    """Recommend concrete checks bound to authorized tools, scope stated.

    Steps: `re_detect` over the post-change window (independent post-fix
    evidence — the loop closes only if the grouping rule stays clean),
    `atlas_resolve` when a structural adapter is wired, and an optional
    `doctor_handoff` for specialist diagnosis. Each step is falsifiable.
    """
    verification_id = verification_id_for(
        incident_id=incident.incident_id,
        hypothesis_id=hypothesis_id,
        at=at,
    )
    rule_kinds = sorted({r.detector_id for r in incident.rule_refs})
    steps = [
        VerificationStep(
            step_id="re_detect",
            kind="re_detect",
            check=("re-run the incident's detection rule(s) over the post-change window"),
            expected_signal=(
                "the grouping rule(s) evaluate clean on the post-change "
                "window — the condition no longer fires"
            ),
            falsification=(
                "the same rule(s) fire again on the post-change window — "
                "the hypothesis (or the fix) is contradicted"
            ),
            scope=f"service:{incident.service_id} rules:{','.join(rule_kinds)}",
            params={
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
            },
        )
    ]
    if structural_available:
        steps.append(
            VerificationStep(
                step_id="atlas_resolve",
                kind="atlas_resolve",
                check=("resolve the service's declared structural identity via Code Atlas"),
                expected_signal=(
                    "the structural counterpart resolves and corroborates "
                    "the hypothesis's claimed path"
                ),
                falsification=(
                    "the declared structural identity cannot resolve — "
                    "identity evidence is absent, not contradicting"
                ),
                scope="read:structural-context",
            )
        )
    if include_doctor:
        steps.append(
            VerificationStep(
                step_id="doctor_handoff",
                kind="doctor_handoff",
                check=("delegate a bounded diagnosis of the incident scope to Forge Doctor API"),
                expected_signal=(
                    "findings return with evidence corroborating the hypothesis's mechanism"
                ),
                falsification=("findings contradict the hypothesis's claimed mechanism"),
                scope="read:api-evidence",
            )
        )
    return VerificationPlan(
        verification_id=verification_id,
        incident_id=incident.incident_id,
        hypothesis_id=hypothesis_id,
        created_at=at,
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Executor — runs steps through the existing ports only.


@runtime_checkable
class ReDetectPort(Protocol):
    """What the executor needs from deterministic re-detection."""

    def rule_refired(
        self, incident: Incident, window: TimeWindow, now: datetime
    ) -> tuple[bool | None, str]:
        """True=rule fired again; False=clean; None=unevaluable."""


class DetectionReDetector:
    """`re_detect` over stored observations — read-only, never persists.

    Runs the same pure `evaluate_rule` path `RunDetection` uses, over the
    persisted rules (or the seeded defaults), and reports whether any
    produced anomaly would group onto this incident's signature. No
    anomaly or incident is written — verification stays independent of
    the fixing path.
    """

    def __init__(self, uow: UnitOfWork, registry: DetectorRegistry | None = None) -> None:
        self._uow = uow
        self._registry = registry or DetectorRegistry()

    def rule_refired(
        self, incident: Incident, window: TimeWindow, now: datetime
    ) -> tuple[bool | None, str]:
        with self._uow as uow:
            rules = uow.detection_rules.list(incident.service_id) or (
                default_rules(incident.service_id, now)
            )
            observations = uow.observations.list_for_service(
                incident.service_id, window.start, window.end
            )
            context = DetectionContext(
                service_id=incident.service_id,
                now=now,
                observations=observations,
                slo_assessments=uow.slo_assessments.list_for_service(incident.service_id),
                sources=uow.sources.list(incident.service_id),
                releases=uow.releases.list(incident.service_id),
                recent_anomalies=uow.anomalies.list_for_service(incident.service_id),
                baselines=summarize_baselines(
                    observations,
                    service_id=incident.service_id,
                    before_window_start=window.start,
                ),
            )
        request = DetectionRequest(
            request_id=detection_request_id(incident.service_id, window, now),
            service_id=incident.service_id,
            window=window,
            now=now,
        )
        evaluated = 0
        for rule in sorted(rules, key=lambda r: r.rule_id):
            detector = self._registry.create(rule)
            evaluation = evaluate_rule(
                rule=rule, detector=detector, request=request, context=context
            )
            evaluated += 1
            for draft in evaluation.anomalies:
                group = group_key_for(draft.service_id, draft.operation_id, detector.detector_id)
                if group == incident.group_key:
                    return (
                        True,
                        f"grouping rule {detector.detector_id} fired again on "
                        f"{window.start.isoformat()}..{window.end.isoformat()}",
                    )
        if evaluated == 0:
            return None, "no detection rules registered for the service"
        return (
            False,
            f"{evaluated} rule(s) evaluated clean on "
            f"{window.start.isoformat()}..{window.end.isoformat()}",
        )


class VerificationExecutor:
    """Executes a plan's steps through the real ports — receipts always.

    `structural`/`doctor` are optional ports: absent adapters produce a
    named `unavailable` receipt on that step, never a fabricated check.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        redetect: ReDetectPort | None = None,
        structural: StructuralContextPort | None = None,
        doctor: DoctorACL | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._uow = uow
        self._redetect = redetect or DetectionReDetector(uow)
        self._structural = structural
        self._doctor = doctor
        self._ws_root = workspace_root

    def execute(self, plan: VerificationPlan, *, now: datetime) -> VerificationResult:
        receipts = [self._run_step(step, plan, now) for step in plan.steps]
        verdict = self._verdict(receipts)
        if not plan.steps:
            verdict = VERDICT_INCONCLUSIVE
        detail = {
            VERDICT_VERIFIED: "independent evidence supports the hypothesis",
            VERDICT_REFUTED: "independent evidence contradicts the hypothesis",
            VERDICT_INCONCLUSIVE: ("no decisive independent evidence — hypothesis unchanged"),
        }[verdict]
        return VerificationResult(
            verification_id=plan.verification_id,
            incident_id=plan.incident_id,
            hypothesis_id=plan.hypothesis_id,
            verdict=verdict,
            receipts=receipts,
            executed_at=now,
            detail=detail,
        )

    @staticmethod
    def _verdict(receipts: list[StepReceipt]) -> str:
        if any(r.status == "falsified" for r in receipts):
            return VERDICT_REFUTED
        if any(r.status == "supporting" for r in receipts):
            return VERDICT_VERIFIED
        return VERDICT_INCONCLUSIVE

    def _run_step(
        self, step: VerificationStep, plan: VerificationPlan, now: datetime
    ) -> StepReceipt:
        try:
            if step.kind == "re_detect":
                return self._re_detect(step, plan, now)
            if step.kind == "atlas_resolve":
                return self._atlas_resolve(step, plan)
            if step.kind == "doctor_handoff":
                return self._doctor_handoff(step, plan, now)
            return StepReceipt(
                step_id=step.step_id,
                kind=step.kind,
                status="refused",
                detail=f"unknown verification tool: {step.kind!r}",
            )
        except Exception as exc:  # a broken check is named, never a crash
            return StepReceipt(
                step_id=step.step_id,
                kind=step.kind,
                status="failed",
                detail=f"{type(exc).__name__}: {exc}",
            )

    def _re_detect(
        self, step: VerificationStep, plan: VerificationPlan, now: datetime
    ) -> StepReceipt:
        try:
            window = TimeWindow(
                start=datetime.fromisoformat(step.params["window_start"]),
                end=datetime.fromisoformat(step.params["window_end"]),
            )
        except (KeyError, ValueError) as exc:
            return StepReceipt(
                step_id=step.step_id,
                kind=step.kind,
                status="refused",
                detail=f"re_detect requires window_start/window_end params: {exc}",
            )
        with self._uow as uow:
            incident = uow.incidents.get(plan.incident_id)
        if incident is None:
            return StepReceipt(
                step_id=step.step_id,
                kind=step.kind,
                status="failed",
                detail=f"incident not found: {plan.incident_id}",
            )
        fired, detail = self._redetect.rule_refired(incident, window, now)
        if fired is None:
            return StepReceipt(
                step_id=step.step_id,
                kind=step.kind,
                status="inconclusive",
                detail=detail,
            )
        if fired:
            return StepReceipt(
                step_id=step.step_id,
                kind=step.kind,
                status="falsified",
                detail=detail,
                evidence_refs=[f"redetect:{window.start.isoformat()}..{window.end.isoformat()}"],
            )
        return StepReceipt(
            step_id=step.step_id,
            kind=step.kind,
            status="supporting",
            detail=detail,
            evidence_refs=[f"redetect:{window.start.isoformat()}..{window.end.isoformat()}"],
        )

    def _atlas_resolve(self, step: VerificationStep, plan: VerificationPlan) -> StepReceipt:
        if self._structural is None:
            return StepReceipt(
                step_id=step.step_id,
                kind=step.kind,
                status="unavailable",
                detail="no structural context adapter configured",
            )
        with self._uow as uow:
            incident = uow.incidents.get(plan.incident_id)
            service = uow.services.get(incident.service_id) if incident is not None else None
        if service is None:
            return StepReceipt(
                step_id=step.step_id,
                kind=step.kind,
                status="failed",
                detail=f"service missing for incident {plan.incident_id}",
            )
        ref = self._structural.resolve_service(service)
        if ref is None:
            return StepReceipt(
                step_id=step.step_id,
                kind=step.kind,
                status="inconclusive",
                detail=(
                    "declared code_atlas_identity unresolved — identity "
                    "evidence absent (ADR-012), not contradicting"
                ),
            )
        edges = self._structural.dependencies(ref.atlas_id)
        return StepReceipt(
            step_id=step.step_id,
            kind=step.kind,
            status="supporting",
            detail=(
                f"structural counterpart resolved: {ref.atlas_id} "
                f"({len(edges)} outgoing structural edges)"
            ),
            evidence_refs=[f"atlas:{ref.atlas_id}"],
        )

    def _doctor_handoff(
        self, step: VerificationStep, plan: VerificationPlan, now: datetime
    ) -> StepReceipt:
        if self._doctor is None:
            return StepReceipt(
                step_id=step.step_id,
                kind=step.kind,
                status="unavailable",
                detail="no doctor adapter configured",
            )
        with self._uow as uow:
            incident = uow.incidents.get(plan.incident_id)
        if incident is None:
            return StepReceipt(
                step_id=step.step_id,
                kind=step.kind,
                status="failed",
                detail=f"incident not found: {plan.incident_id}",
            )
        from api_sentinel.contracts.v1 import InvestigationHandoffV1
        from api_sentinel.integrations.doctor.translate import (
            DOCTOR_FORGE,
            DOCTOR_INVESTIGATION_KIND,
        )

        handoff = InvestigationHandoffV1(
            schema_=InvestigationHandoffV1.expected_schema,
            handoff_id=f"hnd_ver_{plan.verification_id}",
            from_forge="api-sentinel-forge",
            to_forge=DOCTOR_FORGE,
            kind=DOCTOR_INVESTIGATION_KIND,
            payload={
                "service_id": incident.service_id,
                "capabilities": ["api.diagnose"],
                "verification_id": plan.verification_id,
                "hypothesis_id": plan.hypothesis_id,
            },
            constraints={
                "evidence_refs": list(incident.evidence_refs),
                "scopes": [step.scope or "read:api-evidence"],
            },
        )
        result = self._doctor.delegate(
            handoff,
            target=incident.service_id,
            capabilities=["api.diagnose"],
            handoffs_dir=((self._ws_root / "handoffs") if self._ws_root else None),
            now_iso=now.isoformat(),
        )
        status_map = {
            "completed": "supporting",
            "delegated": "inconclusive",  # peer unconfirmed — no evidence yet
            "refused": "refused",
            "timed_out": "unavailable",
            "unavailable": "unavailable",
            "failed": "failed",
        }
        refs = [r for r in (result.request_ref, result.response_ref) if r]
        if result.ingest is not None:
            refs.extend(f"doctor:{f.get('id', 'finding')}" for f in result.ingest.findings[:20])
        return StepReceipt(
            step_id=step.step_id,
            kind=step.kind,
            status=status_map.get(result.status, "failed"),
            detail=result.detail,
            evidence_refs=refs,
        )


def verification_journal_event(
    incident: Incident,
    result: VerificationResult,
    *,
    at: datetime,
    actor: str,
) -> IncidentEvent:
    return make_event(
        incident_id=incident.incident_id,
        kind=EventKind.VERIFICATION_RECORDED,
        at=at,
        actor=actor,
        from_status=incident.status,
        to_status=incident.status,
        note=(
            f"verification {result.verification_id} → {result.verdict} for {result.hypothesis_id}"
        ),
        details={
            "verification_id": result.verification_id,
            "hypothesis_id": result.hypothesis_id,
            "verdict": result.verdict,
            "receipts": str(len(result.receipts)),
        },
        discriminator=f"verification|{result.verification_id}",
    )


# ---------------------------------------------------------------------------
# Use case — plan → execute → persist the record + journal entry.


class RunVerification:
    """Build (or accept) a falsifiable plan, execute it, record the verdict.

    The `VerificationRecord` is persisted on the incident and journaled as
    `verification_recorded`; replaying the same (incident, hypothesis,
    timestamp) reproduces the same verification id — never a duplicate.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        now: datetime,
        *,
        executor: VerificationExecutor | None = None,
        structural: StructuralContextPort | None = None,
        doctor: DoctorACL | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._uow, self._now = uow, now
        self._executor = executor or VerificationExecutor(
            uow,
            structural=structural,
            doctor=doctor,
            workspace_root=workspace_root,
        )
        self._structural = structural

    def execute(
        self,
        *,
        incident_id: str,
        hypothesis_id: str,
        window_start: datetime,
        window_end: datetime,
        include_doctor: bool = False,
        actor: str = "cli",
    ) -> VerificationResult:
        with self._uow as uow:
            incident = uow.incidents.get(incident_id)
            if incident is None:
                raise NotFoundError(f"incident not found: {incident_id}")
            if not any(h.hypothesis_id == hypothesis_id for h in incident.hypotheses):
                raise NotFoundError(
                    f"hypothesis not found: {hypothesis_id}",
                    details={"incident_id": incident_id},
                )
            plan = default_verification_plan(
                incident=incident,
                hypothesis_id=hypothesis_id,
                at=self._now,
                window_start=window_start,
                window_end=window_end,
                structural_available=self._structural is not None,
                include_doctor=include_doctor,
            )
        result = self._executor.execute(plan, now=self._now)
        with self._uow as uow:
            incident = uow.incidents.get(incident_id)
            if incident is None:
                raise NotFoundError(f"incident not found: {incident_id}")
            if not any(v.verification_id == result.verification_id for v in incident.verifications):
                updated = incident.model_copy(
                    update={
                        "verifications": [
                            *incident.verifications,
                            result.to_record(),
                        ],
                        "revision": incident.revision + 1,
                    }
                )
                uow.incidents.put(updated, expected_revision=incident.revision)
                uow.incident_events.append(
                    verification_journal_event(updated, result, at=self._now, actor=actor)
                )
            uow.commit()
        return result
