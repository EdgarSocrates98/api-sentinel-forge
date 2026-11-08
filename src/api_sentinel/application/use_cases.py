"""Use cases — orchestrate ports; deterministic via injected clock/ids."""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import Field

from api_sentinel.application.ports import UnitOfWork
from api_sentinel.detection import (
    Anomaly,
    DetectionContext,
    DetectionRequest,
    DetectorRegistry,
    EvaluationOutcome,
    RuleEvaluation,
    default_rules,
    detection_request_id,
    evaluate_rule,
    materialize_anomaly,
    summarize_baselines,
)
from api_sentinel.domain import (
    ApiService,
    AuditEvent,
    Clock,
    ConflictError,
    EvidenceArtifact,
    IdFactory,
    NotFoundError,
    ServiceEnvironment,
    TelemetrySource,
    ValidationError,
)
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.incidents import (
    OPEN_STATUSES,
    Incident,
    IncidentEvent,
    IncidentStatus,
    attach_anomaly,
    group_key_for,
    open_incident,
    recovery_observed,
    transition,
)
from api_sentinel.metrics import ErrorClassification, TimeWindow
from api_sentinel.reliability import (
    HealthPolicy,
    ServiceHealthReport,
    SliKind,
    SloAssessment,
    SloDefinition,
    SloThresholds,
    assess_service_health,
    evaluate_slo,
)


def _now(clock: Clock) -> datetime:
    return clock.now()


class RegisterService:
    """Idempotent: same (environment, service_name) returns the same service."""

    def __init__(self, uow: UnitOfWork, clock: Clock, ids: IdFactory) -> None:
        self._uow, self._clock, self._ids = uow, clock, ids

    def execute(
        self,
        *,
        service_name: str,
        environment: str = "local",
        namespace: str | None = None,
        display_name: str | None = None,
    ) -> ApiService:
        if not service_name.strip():
            raise ValidationError("service_name must not be empty")
        with self._uow:
            env = self._uow.environments.get_by_name(environment)
            if env is None:
                env = self._uow.environments.put(
                    ServiceEnvironment(
                        environment_id=self._ids.new("env"),
                        name=environment,
                        created_at=_now(self._clock),
                    )
                )
            existing = self._uow.services.get_by_name(env.environment_id, service_name)
            if existing is not None:
                return existing
            service = self._uow.services.put(
                ApiService(
                    service_id=self._ids.new("svc"),
                    service_name=service_name,
                    environment_id=env.environment_id,
                    namespace=namespace,
                    display_name=display_name,
                    created_at=_now(self._clock),
                    updated_at=_now(self._clock),
                )
            )
            self._uow.audit.append(
                AuditEvent(
                    audit_id=self._ids.new("aud"),
                    at=_now(self._clock),
                    actor="cli",
                    action="service.register",
                    target_type="service",
                    target_id=service.service_id,
                )
            )
            self._uow.commit()
            return service


class ListServices:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self) -> list[ApiService]:
        with self._uow:
            return self._uow.services.list()


class RegisterSource:
    def __init__(self, uow: UnitOfWork, clock: Clock, ids: IdFactory) -> None:
        self._uow, self._clock, self._ids = uow, clock, ids

    def execute(
        self, *, kind: str, service_id: str, config: dict[str, str] | None = None
    ) -> TelemetrySource:
        with self._uow:
            if self._uow.services.get(service_id) is None:
                raise NotFoundError(f"service not found: {service_id}")
            source = self._uow.sources.put(
                TelemetrySource(
                    source_id=self._ids.new("src"),
                    kind=kind,
                    service_id=service_id,
                    config=config or {},
                    created_at=_now(self._clock),
                )
            )
            self._uow.commit()
            return source


class StoreEvidence:
    """Persist an evidence artifact against a content-addressed blob."""

    def __init__(self, uow: UnitOfWork, clock: Clock, ids: IdFactory, blobs: object) -> None:
        self._uow, self._clock, self._ids, self._blobs = uow, clock, ids, blobs

    def execute(
        self, *, kind: str, content: bytes, source_id: str | None = None
    ) -> EvidenceArtifact:
        blob_ref = self._blobs.put(content)  # type: ignore[attr-defined]
        digest = hashlib.sha256(content).hexdigest()
        artifact = EvidenceArtifact(
            evidence_id=self._ids.new("ev"),
            kind=kind,
            created_at=_now(self._clock),
            hash=f"sha256:{digest}",
            size_bytes=len(content),
            source_id=source_id,
            blob_ref=blob_ref,
        )
        with self._uow:
            self._uow.evidence.put(artifact)
            self._uow.commit()
        return artifact


class DefineSlo:
    """Create a versioned SloDefinition.

    A revision is immutable: re-defining the same (slo_id, revision) with an
    identical definition is an idempotent no-op; a *different* definition for
    an existing revision is a ConflictError — changing an SLO requires a new
    revision (LAB-015, "rule changes never rewrite historical results").
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, ids: IdFactory) -> None:
        self._uow, self._clock, self._ids = uow, clock, ids

    def execute(
        self,
        *,
        service_id: str,
        target: float,
        window_seconds: int,
        revision: int | None = None,
        sli_kind: SliKind = SliKind.AVAILABILITY,
        operation_id: str | None = None,
        latency_ms: float | None = None,
        error_classification: ErrorClassification | None = None,
        slo_id: str | None = None,
    ) -> SloDefinition:
        if not 0.0 < target < 1.0:
            raise ValidationError(
                "SLO target must be in (0, 1)", details={"target": target}
            )
        if window_seconds <= 0:
            raise ValidationError(
                "SLO window must be positive", details={"window_seconds": window_seconds}
            )
        with self._uow:
            if self._uow.services.get(service_id) is None:
                raise NotFoundError(f"service not found: {service_id}")
            slo_id = slo_id or self._ids.new("slo")
            if revision is None:
                revisions = [
                    d.revision
                    for d in self._uow.slo_definitions.list(service_id)
                    if d.slo_id == slo_id
                ]
                revision = (max(revisions) + 1) if revisions else 1
            if revision < 1:
                raise ValidationError(
                    "SLO revision must be >= 1", details={"revision": revision}
                )
            try:
                definition = SloDefinition(
                    slo_id=slo_id,
                    revision=revision,
                    service_id=service_id,
                    operation_id=operation_id,
                    sli_kind=sli_kind,
                    target=target,
                    window_seconds=window_seconds,
                    thresholds=SloThresholds(latency_ms=latency_ms),
                    error_classification=error_classification or ErrorClassification(),
                    created_at=_now(self._clock),
                )
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            existing = self._uow.slo_definitions.get(slo_id, revision)
            if existing is not None:
                same = existing.model_dump(exclude={"created_at"}) == definition.model_dump(
                    exclude={"created_at"}
                )
                if same:
                    return existing
                raise ConflictError(
                    f"slo revision already exists with a different definition:"
                    f" {slo_id} rev {revision}",
                    details={"slo_id": slo_id, "revision": revision},
                )
            self._uow.slo_definitions.put(definition)
            self._uow.audit.append(
                AuditEvent(
                    audit_id=self._ids.new("aud"),
                    at=_now(self._clock),
                    actor="cli",
                    action="slo.define",
                    target_type="slo",
                    target_id=f"{slo_id}@{revision}",
                    details={"service_id": service_id, "target": str(target)},
                )
            )
            self._uow.commit()
            return definition


class EvaluateSlo:
    """Reproducible window evaluation; persisted idempotently per slot."""

    def __init__(self, uow: UnitOfWork, clock: Clock, ids: IdFactory) -> None:
        self._uow, self._clock, self._ids = uow, clock, ids

    def execute(
        self,
        *,
        slo_id: str,
        window_start: datetime,
        window_end: datetime,
        revision: int | None = None,
    ) -> SloAssessment:
        with self._uow:
            definition = self._uow.slo_definitions.get(slo_id, revision)
            if definition is None:
                raise NotFoundError(
                    f"slo not found: {slo_id}",
                    details={"slo_id": slo_id, "revision": revision},
                )
            try:
                window = TimeWindow(start=window_start, end=window_end)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            observations = self._uow.observations.list_for_service(
                definition.service_id, window_start, window_end
            )
            assessment = evaluate_slo(
                definition=definition,
                observations=observations,
                window=window,
                evaluated_at=_now(self._clock),
            )
            self._uow.slo_assessments.put(assessment)
            self._uow.audit.append(
                AuditEvent(
                    audit_id=self._ids.new("aud"),
                    at=_now(self._clock),
                    actor="cli",
                    action="slo.evaluate",
                    target_type="slo_assessment",
                    target_id=assessment.assessment_id,
                    details={
                        "slo_id": slo_id,
                        "revision": str(definition.revision),
                        "state": assessment.state.value,
                    },
                )
            )
            self._uow.commit()
            return assessment


class ListSloDefinitions:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, *, service_id: str | None = None) -> list[SloDefinition]:
        with self._uow:
            return self._uow.slo_definitions.list(service_id)


class GetServiceHealth:
    """Per-service health: state + latest indicators + unknowns (LAB-007)."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def execute(
        self, *, service_id: str, stale_after_seconds: int = 300
    ) -> ServiceHealthReport:
        with self._uow:
            if self._uow.services.get(service_id) is None:
                raise NotFoundError(f"service not found: {service_id}")
            source_ids = [
                s.source_id for s in self._uow.sources.list(service_id)
            ]
            observations = self._uow.observations.list_for_service(service_id)
            return assess_service_health(
                service_id=service_id,
                observations=observations,
                source_ids=source_ids,
                now=_now(self._clock),
                policy=HealthPolicy(stale_after_seconds=stale_after_seconds),
            )


# ---------------------------------------------------------------------------
# Detection & incident lifecycle (Cycle 4)


class DetectionRunReport(FrozenModel):
    """Honest report of one detection run — fired, deduplicated, suppressed."""

    service_id: str
    window_start: datetime
    window_end: datetime
    evaluated_at: datetime
    request_id: str
    evaluations: list[RuleEvaluation] = Field(default_factory=list)
    anomalies: list[Anomaly] = Field(default_factory=list)
    deduplicated: list[str] = Field(default_factory=list)
    incidents_created: list[Incident] = Field(default_factory=list)
    incidents_updated: list[str] = Field(default_factory=list)
    recovery_observed: list[str] = Field(default_factory=list)
    rules_seeded: bool = False


class RunDetection:
    """Run registered versioned rules over a window; dedup into incidents.

    Loads the service's persisted `DetectionRule`s (seeding the revision-1
    built-ins on first run — recorded via audit), evaluates each through the
    pure `evaluate_rule` policy wrapper, persists anomalies under their
    deterministic ids (replay = idempotent, never a duplicate), and groups
    them into incidents by (service, operation scope, detector signature,
    unresolved status) — the documented multi-key match of docs/09.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        ids: IdFactory,
        registry: DetectorRegistry | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._ids = ids
        self._registry = registry or DetectorRegistry()

    def execute(
        self,
        *,
        service_id: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        now: datetime | None = None,
    ) -> DetectionRunReport:
        with self._uow as uow:
            service = uow.services.get(service_id)
            if service is None:
                raise NotFoundError(f"service not found: {service_id}")
            now = now or _now(self._clock)

            rules = uow.detection_rules.list(service_id)
            seeded = False
            if not rules:
                rules = default_rules(service_id, now)
                for rule in rules:
                    uow.detection_rules.put(rule)
                seeded = True

            observations = uow.observations.list_for_service(service_id)
            if window_start is None or window_end is None:
                if not observations:
                    raise ValidationError(
                        "no stored observations; pass an explicit window",
                        details={"service_id": service_id},
                    )
                window_start = min(o.window_start for o in observations)
                window_end = max(o.window_end for o in observations)
            try:
                window = TimeWindow(start=window_start, end=window_end)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc

            context = DetectionContext(
                service_id=service_id,
                now=now,
                observations=observations,
                slo_assessments=uow.slo_assessments.list_for_service(service_id),
                sources=uow.sources.list(service_id),
                releases=uow.releases.list(service_id),
                recent_anomalies=uow.anomalies.list_for_service(service_id),
                baselines=summarize_baselines(
                    observations,
                    service_id=service_id,
                    before_window_start=window.start,
                ),
            )
            request = DetectionRequest(
                request_id=detection_request_id(service_id, window, now),
                service_id=service_id,
                window=window,
                now=now,
            )

            evaluations: list[RuleEvaluation] = []
            persisted: list[Anomaly] = []
            deduplicated: list[str] = []
            created: list[Incident] = []
            updated_ids: list[str] = []
            fired_group_keys: set[str] = set()
            evaluated_kinds: set[str] = set()

            recent_by_id = {a.anomaly_id: a for a in context.recent_anomalies}
            for rule in sorted(rules, key=lambda r: r.rule_id):
                detector = self._registry.create(rule)  # named error on bad kind
                evaluation = evaluate_rule(
                    rule=rule, detector=detector, request=request, context=context
                )
                evaluations.append(evaluation)
                if evaluation.outcome in (
                    EvaluationOutcome.SKIPPED_DISABLED,
                    EvaluationOutcome.SKIPPED_PLANNED_SUPPRESSION,
                ):
                    continue
                evaluated_kinds.add(detector.detector_id)
                # Replays: confirmed drafts whose deterministic id is already
                # persisted — counted as deduplicated, and their group stays
                # "firing" so a clean-window recovery is never faked.
                for anomaly_id in evaluation.recorded:
                    deduplicated.append(anomaly_id)
                    prior = recent_by_id.get(anomaly_id)
                    if prior is not None:
                        fired_group_keys.add(
                            group_key_for(
                                prior.service_id,
                                prior.operation_id,
                                prior.rule.detector_id,
                            )
                        )
                for draft in evaluation.anomalies:
                    anomaly = materialize_anomaly(
                        rule=rule, detector=detector, draft=draft
                    )
                    existing = uow.anomalies.get(anomaly.anomaly_id)
                    if existing is not None:
                        deduplicated.append(anomaly.anomaly_id)
                        anomaly = existing  # keep the stored row
                    else:
                        uow.anomalies.put(anomaly)
                        persisted.append(anomaly)
                    fired_group_keys.add(
                        group_key_for(
                            anomaly.service_id,
                            anomaly.operation_id,
                            detector.detector_id,
                        )
                    )
                    incident_id = self._attach_to_incident(
                        uow,
                        service=service,
                        anomaly=anomaly,
                        now=now,
                        created=created,
                        updated_ids=updated_ids,
                    )
                    uow.anomalies.attach_incident(anomaly.anomaly_id, incident_id)

            recovery_ids = self._journal_recoveries(
                uow,
                service_id=service_id,
                window=window,
                now=now,
                evaluated_kinds=evaluated_kinds,
                fired_group_keys=fired_group_keys,
            )

            if seeded:
                uow.audit.append(
                    AuditEvent(
                        audit_id=self._ids.new("aud"),
                        at=now,
                        actor="cli",
                        action="detection.seed_defaults",
                        target_type="service",
                        target_id=service_id,
                        details={"rules": ",".join(r.rule_id for r in rules)},
                    )
                )
            uow.audit.append(
                AuditEvent(
                    audit_id=self._ids.new("aud"),
                    at=now,
                    actor="cli",
                    action="detection.run",
                    target_type="service",
                    target_id=service_id,
                    details={
                        "window_start": window.start.isoformat(),
                        "window_end": window.end.isoformat(),
                        "anomalies": str(len(persisted)),
                        "incidents_created": str(len(created)),
                    },
                )
            )
            uow.commit()
            return DetectionRunReport(
                service_id=service_id,
                window_start=window.start,
                window_end=window.end,
                evaluated_at=now,
                request_id=request.request_id,
                evaluations=evaluations,
                anomalies=persisted,
                deduplicated=sorted(deduplicated),
                incidents_created=created,
                incidents_updated=sorted(set(updated_ids)),
                recovery_observed=sorted(recovery_ids),
                rules_seeded=seeded,
            )

    def _attach_to_incident(
        self,
        uow: UnitOfWork,
        *,
        service: ApiService,
        anomaly: Anomaly,
        now: datetime,
        created: list[Incident],
        updated_ids: list[str],
    ) -> str:
        """Dedup: attach to the open incident for the group, else open one."""
        if anomaly.incident_id is not None:
            return anomaly.incident_id  # replay: linkage already persisted
        group_key = group_key_for(
            anomaly.service_id, anomaly.operation_id, anomaly.rule.detector_id
        )
        incident = uow.incidents.find_open(service.service_id, group_key)
        if incident is None:
            incident, events = open_incident(
                anomaly=anomaly, service_name=service.service_name, at=now
            )
            uow.incidents.put(incident)
            created.append(incident)
        else:
            updated, events = attach_anomaly(incident, anomaly, at=now)
            if updated.revision != incident.revision:
                uow.incidents.put(updated, expected_revision=incident.revision)
                updated_ids.append(updated.incident_id)
        for event in events:
            uow.incident_events.append(event)
        return incident.incident_id

    def _journal_recoveries(
        self,
        uow: UnitOfWork,
        *,
        service_id: str,
        window: TimeWindow,
        now: datetime,
        evaluated_kinds: set[str],
        fired_group_keys: set[str],
    ) -> list[str]:
        """Recovery observed: an open incident's grouping rule evaluated clean
        over this window — journaled as evidence, never a silent resolve."""
        out: list[str] = []
        for incident in uow.incidents.list(service_id=service_id):
            if incident.status not in OPEN_STATUSES:
                continue
            kind = incident.group_key.rsplit("|", 1)[-1]
            if kind not in evaluated_kinds:
                continue
            if incident.group_key in fired_group_keys:
                continue
            rule_ref = next(
                (r for r in incident.rule_refs if r.detector_id == kind), None
            )
            if rule_ref is None:
                continue
            event = recovery_observed(
                incident,
                at=now,
                rule_ref=rule_ref,
                window_start=window.start,
                window_end=window.end,
            )
            uow.incident_events.append(event)  # deterministic id — idempotent
            out.append(incident.incident_id)
        return out


class IncidentView(FrozenModel):
    """`incident show` payload: current row + journal + linked anomalies."""

    incident: Incident
    events: list[IncidentEvent] = Field(default_factory=list)
    anomalies: list[Anomaly] = Field(default_factory=list)


class ListIncidents:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self,
        *,
        status: IncidentStatus | None = None,
        service_id: str | None = None,
        open_only: bool = False,
    ) -> list[Incident]:
        with self._uow as uow:
            incidents = uow.incidents.list(status=status, service_id=service_id)
            if open_only:
                incidents = [i for i in incidents if i.status in OPEN_STATUSES]
            return incidents


class ShowIncident:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, *, incident_id: str) -> IncidentView:
        with self._uow as uow:
            incident = uow.incidents.get(incident_id)
            if incident is None:
                raise NotFoundError(f"incident not found: {incident_id}")
            return IncidentView(
                incident=incident,
                events=uow.incident_events.list(incident_id),
                anomalies=uow.anomalies.list_for_incident(incident_id),
            )


class TransitionIncident:
    """ack/resolve/reopen — every transition is a journaled event.

    The lifecycle validates the move; the revision guard enforces
    concurrency. A CLOSED incident accepts no transition (journal only).
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, ids: IdFactory) -> None:
        self._uow, self._clock, self._ids = uow, clock, ids

    def execute(
        self,
        *,
        incident_id: str,
        to_status: IncidentStatus,
        actor: str = "cli",
        note: str | None = None,
    ) -> Incident:
        with self._uow as uow:
            incident = uow.incidents.get(incident_id)
            if incident is None:
                raise NotFoundError(f"incident not found: {incident_id}")
            updated, event = transition(
                incident,
                to_status,
                at=_now(self._clock),
                actor=actor,
                note=note,
            )
            uow.incidents.put(updated, expected_revision=incident.revision)
            uow.incident_events.append(event)
            uow.audit.append(
                AuditEvent(
                    audit_id=self._ids.new("aud"),
                    at=_now(self._clock),
                    actor=actor,
                    action=f"incident.{to_status.value.lower()}",
                    target_type="incident",
                    target_id=incident_id,
                    details={
                        "from_status": incident.status.value,
                        "to_status": to_status.value,
                    },
                )
            )
            uow.commit()
            return updated
