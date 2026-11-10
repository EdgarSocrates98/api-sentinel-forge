"""Use cases — orchestrate ports; deterministic via injected clock/ids."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from pydantic import Field

from api_sentinel.application.jobs import (
    IngestionJob,
    IngestionJobStatus,
    Investigation,
    InvestigationPack,
    InvestigationStatus,
    StructuralContextView,
)
from api_sentinel.application.ports import UnitOfWork
from api_sentinel.detection import (
    Anomaly,
    AnomalyStatus,
    DetectionContext,
    DetectionRequest,
    DetectorRegistry,
    EvaluationOutcome,
    RuleEvaluation,
    default_rules,
    detection_request_id,
    evaluate_rule,
    materialize_anomaly,
    operation_ref,
    summarize_baselines,
)
from api_sentinel.domain import (
    ApiOperation,
    ApiService,
    AuditEvent,
    ChangeEvent,
    Clock,
    ConflictError,
    DependencyObservation,
    EvidenceArtifact,
    ExternalRef,
    IdFactory,
    NotFoundError,
    QualityState,
    ServiceEnvironment,
    TelemetrySource,
    ValidationError,
)
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.quality import worst_of
from api_sentinel.incidents import (
    OPEN_STATUSES,
    Hypothesis,
    Incident,
    IncidentEvent,
    IncidentStatus,
    VerificationRecord,
    attach_anomaly,
    group_key_for,
    open_incident,
    record_action,
    recovery_observed,
    transition,
)
from api_sentinel.ingestion import IngestionPipeline
from api_sentinel.integrations.atlas.port import StructuralContextPort
from api_sentinel.integrations.correlate import CorrelationReport, correlate_edges
from api_sentinel.metrics import (
    ErrorClassification,
    TimeWindow,
    WindowAggregate,
    compute_window_aggregates,
)
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
                source_kinds={
                    s.source_id: s.kind
                    for s in self._uow.sources.list(definition.service_id)
                },
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


# ---------------------------------------------------------------------------
# Portal API operations (Cycle 6) — composite reads + bounded sync jobs.
#
# These use cases serve the local /v1 HTTP surface (docs/04, ADR-011): they
# compose the existing bounded contexts (metrics, reliability, incidents,
# ingestion, integrations) — they never reimplement them. Every read carries
# the honest vocabulary (AVAILABLE|UNAVAILABLE|PARTIAL|UNVERIFIED|UNKNOWN)
# plus explicit unknowns; jobs are bounded-synchronous, idempotent and
# audited.


class ServiceCoverage(FrozenModel):
    """Coverage indicators for one service — real inventory, never demo data."""

    sources_registered: int = 0
    sources_observed: int = 0
    observations: int = 0
    latest_window_end: datetime | None = None


class ServiceSummary(FrozenModel):
    """One `/v1/services` row: identity + honest health + coverage."""

    service: ApiService
    health: ServiceHealthReport
    coverage: ServiceCoverage
    open_incidents: int = 0
    slo_definitions: int = 0


def _service_health_coverage(
    uow: UnitOfWork,
    service_id: str,
    *,
    now: datetime,
    stale_after_seconds: int = 300,
) -> tuple[ServiceHealthReport, ServiceCoverage]:
    source_ids = [s.source_id for s in uow.sources.list(service_id)]
    observations = uow.observations.list_for_service(service_id)
    health = assess_service_health(
        service_id=service_id,
        observations=observations,
        source_ids=source_ids,
        now=now,
        policy=HealthPolicy(stale_after_seconds=stale_after_seconds),
    )
    coverage = ServiceCoverage(
        sources_registered=len(source_ids),
        sources_observed=len({o.source_id for o in observations}),
        observations=len(observations),
        latest_window_end=max(
            (o.window_end for o in observations), default=None
        ),
    )
    return health, coverage


def _open_incident_count(uow: UnitOfWork, service_id: str) -> int:
    return sum(
        1
        for i in uow.incidents.list(service_id=service_id)
        if i.status in OPEN_STATUSES
    )


class ListServiceSummaries:
    """`GET /v1/services` — every registered service with health + coverage."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def execute(self) -> list[ServiceSummary]:
        with self._uow as uow:
            now = _now(self._clock)
            return [
                self._one(uow, service, now) for service in uow.services.list()
            ]

    @staticmethod
    def _one(
        uow: UnitOfWork, service: ApiService, now: datetime
    ) -> ServiceSummary:
        health, coverage = _service_health_coverage(
            uow, service.service_id, now=now
        )
        return ServiceSummary(
            service=service,
            health=health,
            coverage=coverage,
            open_incidents=_open_incident_count(uow, service.service_id),
            slo_definitions=len(
                uow.slo_definitions.list(service.service_id)
            ),
        )


class ServiceDetail(FrozenModel):
    """`GET /v1/services/{id}` — full health report + sources + counts."""

    service: ApiService
    health: ServiceHealthReport
    coverage: ServiceCoverage
    sources: list[TelemetrySource] = Field(default_factory=list)
    open_incidents: int = 0
    slo_definitions: int = 0


class GetServiceDetail:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def execute(self, *, service_id: str) -> ServiceDetail:
        with self._uow as uow:
            service = uow.services.get(service_id)
            if service is None:
                raise NotFoundError(f"service not found: {service_id}")
            health, coverage = _service_health_coverage(
                uow, service_id, now=_now(self._clock)
            )
            return ServiceDetail(
                service=service,
                health=health,
                coverage=coverage,
                sources=uow.sources.list(service_id),
                open_incidents=_open_incident_count(uow, service_id),
                slo_definitions=len(uow.slo_definitions.list(service_id)),
            )


class OperationView(FrozenModel):
    """One identified operation — declared inventory and/or observed series.

    `provenance` keeps the two honest: `declared` (api_operations row),
    `observed` (seen in runtime telemetry), `declared+observed` (both).
    """

    operation_id: str | None = None
    operation_ref: str | None = None
    provenance: str  # declared | observed | declared+observed
    protocol: str | None = None
    method: str | None = None
    route: str | None = None
    observations: int = 0
    sources: list[str] = Field(default_factory=list)
    latest_window_end: datetime | None = None
    quality: QualityState = QualityState.UNKNOWN


def _operation_label(op: ApiOperation) -> str | None:
    ident = op.identity
    if ident.method and ident.route:
        return f"{ident.method} {ident.route}"
    return ident.route or ident.method or op.normalized_route


class ListServiceOperations:
    """`GET /v1/services/{id}/operations` — identified operations only."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, *, service_id: str) -> list[OperationView]:
        with self._uow as uow:
            if uow.services.get(service_id) is None:
                raise NotFoundError(f"service not found: {service_id}")
            declared = uow.operations.list(service_id)
            observations = uow.observations.list_for_service(service_id)

        groups: dict[str | None, list[Any]] = {}
        for obs in observations:
            groups.setdefault(obs.operation_id, []).append(obs)

        views: list[OperationView] = []
        for op in declared:
            group = groups.pop(op.operation_id, [])
            views.append(
                OperationView(
                    operation_id=op.operation_id,
                    operation_ref=_operation_label(op),
                    provenance="declared+observed" if group else "declared",
                    protocol=op.identity.protocol,
                    method=op.identity.method,
                    route=op.identity.route,
                    **self._stats(group),
                )
            )
        for operation_id, group in groups.items():
            latest = max(group, key=lambda o: o.window_end)
            views.append(
                OperationView(
                    operation_id=operation_id,
                    operation_ref=operation_ref(latest.attributes, operation_id),
                    provenance="observed",
                    **self._stats(group),
                )
            )
        views.sort(
            key=lambda v: (v.operation_ref or "", v.operation_id or "")
        )
        return views

    @staticmethod
    def _stats(group: list[Any]) -> dict[str, Any]:
        if not group:
            return {}
        return {
            "observations": len(group),
            "sources": sorted({o.source_id for o in group}),
            "latest_window_end": max(o.window_end for o in group),
            "quality": worst_of([o.quality for o in group]),
        }


class MetricsWindow(FrozenModel):
    """One observation window with its per-(operation, source) aggregates."""

    window_start: datetime
    window_end: datetime
    duration_seconds: float
    aggregates: list[WindowAggregate] = Field(default_factory=list)


class MetricsGap(FrozenModel):
    """A visible data gap between two consecutive observed windows."""

    after_window_end: datetime
    before_window_start: datetime
    estimated_missing_windows: int


class ServiceMetrics(FrozenModel):
    """`GET /v1/services/{id}/metrics` — aggregated series, honest sampling.

    `units` is explicit per docs/04; `gaps` marks missing coverage visibly;
    `qualities` + `excluded_by_reason` carry sampling/gap indicators (SAMPLED
    and INVALID observations never feed rates — LAB-008 — and are reported).
    """

    service_id: str
    units: dict[str, str]
    windows: list[MetricsWindow] = Field(default_factory=list)
    gaps: list[MetricsGap] = Field(default_factory=list)
    observations: int = 0
    qualities: dict[str, int] = Field(default_factory=dict)
    excluded_by_reason: dict[str, int] = Field(default_factory=dict)
    truncated: bool = False
    unknowns: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


METRIC_UNITS: dict[str, str] = {
    "latency": "ms",
    "request_rate": "requests/second",
    "ratio": "fraction",
    "requests": "requests",
    "duration": "seconds",
}


class GetServiceMetrics:
    """Aggregate stored observations per window — bounded to the most recent."""

    MAX_WINDOWS = 240

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self,
        *,
        service_id: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        operation_id: str | None = None,
        max_windows: int = 60,
    ) -> ServiceMetrics:
        if not 1 <= max_windows <= self.MAX_WINDOWS:
            raise ValidationError(
                f"max_windows must be in [1, {self.MAX_WINDOWS}]",
                details={"max_windows": max_windows},
            )
        with self._uow as uow:
            if uow.services.get(service_id) is None:
                raise NotFoundError(f"service not found: {service_id}")
            observations = uow.observations.list_for_service(
                service_id, window_start, window_end
            )
        if operation_id is not None:
            observations = [
                o for o in observations if o.operation_id == operation_id
            ]

        unknowns: list[str] = []
        if not observations:
            unknowns.append(
                "no_observations"
                if operation_id is None
                else "no_observations_for_operation"
            )

        groups: dict[tuple[datetime, datetime], list[Any]] = {}
        for obs in observations:
            groups.setdefault((obs.window_start, obs.window_end), []).append(obs)
        ordered = sorted(groups.items(), key=lambda kv: kv[0][1])
        truncated = len(ordered) > max_windows
        ordered = ordered[-max_windows:]

        windows: list[MetricsWindow] = []
        excluded: dict[str, int] = {}
        limitations: list[str] = []
        qualities: dict[str, int] = {}
        for (w_start, w_end), group in ordered:
            for obs in group:
                q = obs.quality.value
                qualities[q] = qualities.get(q, 0) + 1
            aggregates = compute_window_aggregates(
                group, TimeWindow(start=w_start, end=w_end), by_operation=True
            )
            for agg in aggregates:
                limitations.extend(agg.limitations)
                for reason, count in agg.excluded_by_reason.items():
                    excluded[reason] = excluded.get(reason, 0) + count
            windows.append(
                MetricsWindow(
                    window_start=w_start,
                    window_end=w_end,
                    duration_seconds=(w_end - w_start).total_seconds(),
                    aggregates=aggregates,
                )
            )

        return ServiceMetrics(
            service_id=service_id,
            units=dict(METRIC_UNITS),
            windows=windows,
            gaps=self._gaps([(k[0], k[1]) for k, _ in ordered]),
            observations=sum(len(g) for _, g in ordered),
            qualities=qualities,
            excluded_by_reason=excluded,
            truncated=truncated,
            unknowns=unknowns,
            limitations=sorted(set(limitations)),
        )

    @staticmethod
    def _gaps(
        windows: list[tuple[datetime, datetime]],
    ) -> list[MetricsGap]:
        """Visible gaps: a missing slot is reported, never silently skipped."""
        if len(windows) < 2:
            return []
        deltas = [
            (b[0] - a[0]).total_seconds()
            for a, b in pairwise(windows)
        ]
        positive = [d for d in deltas if d > 0]
        if not positive:
            return []
        nominal = min(positive)
        gaps: list[MetricsGap] = []
        for (a_start, a_end), (b_start, _) in pairwise(windows):
            delta = (b_start - a_start).total_seconds()
            if delta > nominal * 1.5:
                gaps.append(
                    MetricsGap(
                        after_window_end=a_end,
                        before_window_start=b_start,
                        estimated_missing_windows=max(
                            1, round(delta / nominal) - 1
                        ),
                    )
                )
        return gaps


class SloPanelEntry(FrozenModel):
    """One SLO definition + bounded windowed assessment history.

    Every assessment carries the `revision` that produced it (LAB-015 —
    historical results are never rewritten, only superseded by a new
    revision).
    """

    definition: SloDefinition
    latest_assessment: SloAssessment | None = None
    assessments: list[SloAssessment] = Field(default_factory=list)


class ServiceSloPanel(FrozenModel):
    service_id: str
    slos: list[SloPanelEntry] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class GetServiceSlos:
    """`GET /v1/services/{id}/slos` — objectives, budgets, windowed history."""

    HISTORY_LIMIT = 50

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, *, service_id: str) -> ServiceSloPanel:
        with self._uow as uow:
            if uow.services.get(service_id) is None:
                raise NotFoundError(f"service not found: {service_id}")
            entries: list[SloPanelEntry] = []
            for definition in uow.slo_definitions.list(service_id):
                history = [
                    a
                    for a in uow.slo_assessments.list(definition.slo_id)
                    if a.revision == definition.revision
                ]
                bounded = history[-self.HISTORY_LIMIT :]
                entries.append(
                    SloPanelEntry(
                        definition=definition,
                        latest_assessment=bounded[-1] if bounded else None,
                        assessments=bounded,
                    )
                )
            unknowns = [] if entries else ["no_slo_definitions"]
            return ServiceSloPanel(
                service_id=service_id, slos=entries, unknowns=unknowns
            )


class ProductForgeLink(FrozenModel):
    """The Product Forge correspondence verdict (ADR-011/Cycle-6 gate).

    `proven` only when an external_ref carries provenance `verified` — a
    declared ref is surfaced as `declared`; absent refs are `unproven`.
    Identity is never resolved by name matching.
    """

    state: str  # proven | declared | unproven
    ref: ExternalRef | None = None
    reason: str = ""


class ServiceLinks(FrozenModel):
    """`GET /v1/services/{id}/links` — proven external_refs only."""

    service_id: str
    links: list[ExternalRef] = Field(default_factory=list)
    correspondence: ProductForgeLink
    unknowns: list[str] = Field(default_factory=list)


def _is_product_forge_ref(ref: ExternalRef) -> bool:
    return ref.kind == "pf_api" or ref.kind.startswith("product_forge")


class GetServiceLinks:
    """External identity links with their provenance — never name-matched."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, *, service_id: str) -> ServiceLinks:
        with self._uow as uow:
            service = uow.services.get(service_id)
            if service is None:
                raise NotFoundError(f"service not found: {service_id}")
            links = sorted(
                service.external_refs, key=lambda r: (r.kind, r.ref)
            )
        pf = next((r for r in links if _is_product_forge_ref(r)), None)
        unknowns: list[str] = []
        if pf is None:
            correspondence = ProductForgeLink(
                state="unproven",
                reason="no product-forge external_ref on the canonical "
                "identity — correspondence is never inferred by name "
                "matching (ADR-011/ADR-012)",
            )
            unknowns.append("product_forge_correspondence_unproven")
        elif pf.provenance == "verified":
            correspondence = ProductForgeLink(
                state="proven", ref=pf,
                reason="verified identity correspondence recorded on the "
                "canonical service identity",
            )
        else:
            correspondence = ProductForgeLink(
                state="declared", ref=pf,
                reason=f"external_ref provenance is {pf.provenance!r}, not "
                "verified — the Portal may display the link but must not "
                "activate the Product Forge experience",
            )
        return ServiceLinks(
            service_id=service_id,
            links=links,
            correspondence=correspondence,
            unknowns=unknowns,
        )


class IncidentTimeline(FrozenModel):
    """`GET /v1/incidents/{id}/timeline` — journal + related events.

    Cycle 8 surfaces the full investigation record: governed investigation
    attempts, the hypothesis ladder (confidence states, directional
    evidence) and verification records alongside journal entries and
    change/dependency events — the incident's whole evidence trail.
    """

    incident: Incident
    events: list[IncidentEvent] = Field(default_factory=list)
    change_events: list[ChangeEvent] = Field(default_factory=list)
    dependency_observations: list[DependencyObservation] = Field(
        default_factory=list
    )
    investigations: list[Investigation] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    verifications: list[VerificationRecord] = Field(default_factory=list)
    truncated: bool = False
    unknowns: list[str] = Field(default_factory=list)


class GetIncidentTimeline:
    """Journal entries plus changes/observed edges since first observation."""

    RELATED_LIMIT = 200

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, *, incident_id: str) -> IncidentTimeline:
        with self._uow as uow:
            incident = uow.incidents.get(incident_id)
            if incident is None:
                raise NotFoundError(f"incident not found: {incident_id}")
            events = uow.incident_events.list(incident_id)
            changes = uow.change_events.list_for_service(
                incident.service_id,
                at_start=incident.first_observed_at,
                at_end=incident.resolved_at,
            )
            edges = uow.dependency_observations.list_for_service(
                incident.service_id, window_start=incident.first_observed_at
            )
            investigations = uow.investigations.list_for_incident(incident_id)
        truncated = (
            len(changes) > self.RELATED_LIMIT
            or len(edges) > self.RELATED_LIMIT
        )
        unknowns: list[str] = []
        if truncated:
            unknowns.append(
                f"related_events_truncated_at_{self.RELATED_LIMIT}"
            )
        return IncidentTimeline(
            incident=incident,
            events=events,
            change_events=changes[: self.RELATED_LIMIT],
            dependency_observations=edges[: self.RELATED_LIMIT],
            investigations=investigations[: self.RELATED_LIMIT],
            hypotheses=incident.hypotheses,
            verifications=incident.verifications,
            truncated=truncated,
            unknowns=unknowns,
        )


class ListAnomalies:
    """`GET /v1/anomalies` — the anomaly feed, filtered and bounded."""

    MAX_LIMIT = 1000

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self,
        *,
        service_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> tuple[list[Anomaly], bool]:
        parsed: AnomalyStatus | None = None
        if status is not None:
            try:
                parsed = AnomalyStatus(status.strip().upper())
            except ValueError as exc:
                raise ValidationError(
                    f"invalid anomaly status: {status!r}",
                    details={
                        "expected": sorted(s.value for s in AnomalyStatus)
                    },
                ) from exc
        if not 1 <= limit <= self.MAX_LIMIT:
            raise ValidationError(
                f"limit must be in [1, {self.MAX_LIMIT}]",
                details={"limit": limit},
            )
        with self._uow as uow:
            if service_id is not None and uow.services.get(service_id) is None:
                raise NotFoundError(f"service not found: {service_id}")
            rows = uow.anomalies.list_filtered(
                service_id=service_id, status=parsed, limit=limit + 1
            )
        truncated = len(rows) > limit
        return rows[:limit], truncated


class IncidentDetail(FrozenModel):
    """`GET /v1/incidents/{id}` — investigation detail (docs/04).

    Incident + journal + linked anomalies + *resolvable* evidence metadata.
    Unresolvable refs are named under `evidence_unresolved`, never dropped.
    """

    incident: Incident
    events: list[IncidentEvent] = Field(default_factory=list)
    anomalies: list[Anomaly] = Field(default_factory=list)
    evidence: list[EvidenceArtifact] = Field(default_factory=list)
    evidence_unresolved: list[str] = Field(default_factory=list)


def _resolve_evidence(
    uow: UnitOfWork, refs: list[str]
) -> tuple[list[EvidenceArtifact], list[str]]:
    found: list[EvidenceArtifact] = []
    unresolved: list[str] = []
    for ref in refs:
        artifact = uow.evidence.get(ref)
        if artifact is None:
            unresolved.append(ref)
        else:
            found.append(artifact)
    return found, unresolved


class GetIncidentDetail:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, *, incident_id: str) -> IncidentDetail:
        with self._uow as uow:
            incident = uow.incidents.get(incident_id)
            if incident is None:
                raise NotFoundError(f"incident not found: {incident_id}")
            events = uow.incident_events.list(incident_id)
            anomalies = uow.anomalies.list_for_incident(incident_id)
            refs = sorted(
                set(incident.evidence_refs)
                | {r for a in anomalies for r in a.evidence_refs}
            )
            evidence, unresolved = _resolve_evidence(uow, refs)
            return IncidentDetail(
                incident=incident,
                events=events,
                anomalies=anomalies,
                evidence=evidence,
                evidence_unresolved=unresolved,
            )


class GetEvidenceMetadata:
    """`GET /v1/evidence/{id}` — authorized metadata read, audited.

    Metadata only — blob content is never served over the API (docs/07
    evidence-access audit: the read itself is journaled).
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, ids: IdFactory) -> None:
        self._uow, self._clock, self._ids = uow, clock, ids

    def execute(self, *, evidence_id: str, actor: str = "api") -> EvidenceArtifact:
        with self._uow as uow:
            artifact = uow.evidence.get(evidence_id)
            if artifact is None:
                raise NotFoundError(f"evidence not found: {evidence_id}")
            uow.audit.append(
                AuditEvent(
                    audit_id=self._ids.new("aud"),
                    at=_now(self._clock),
                    actor=actor,
                    action="evidence.read",
                    target_type="evidence",
                    target_id=evidence_id,
                    details={"kind": artifact.kind},
                )
            )
            uow.commit()
            return artifact


# --- Investigations & ingestion jobs (bounded synchronous writes) -----------


class StartInvestigation:
    """`POST /v1/incidents/{id}/investigations` — bounded context pack.

    Synchronous by design: assembling the pack is a handful of local reads.
    Idempotent on a client key (deterministic id), journaled on the incident
    (ACTION_RECORDED), audited. The structural slice uses the injected Atlas
    port when — and only when — it has data; otherwise the field is
    `UNAVAILABLE`/`UNRESOLVED`, never fabricated.
    """

    EVENT_CAP = 200
    ANOMALY_CAP = 100
    EVIDENCE_CAP = 200
    EDGE_CAP = 200

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        ids: IdFactory,
        structural: StructuralContextPort | None = None,
        structural_reason: str = "",
    ) -> None:
        self._uow, self._clock, self._ids = uow, clock, ids
        self._structural = structural
        self._structural_reason = structural_reason

    def execute(
        self,
        *,
        incident_id: str,
        idempotency_key: str | None = None,
        actor: str = "api",
        note: str | None = None,
    ) -> tuple[Investigation, bool]:
        """Returns (investigation, replayed)."""
        with self._uow as uow:
            if idempotency_key:
                existing = uow.investigations.get_by_idempotency_key(
                    idempotency_key
                )
                if existing is not None:
                    return existing, True
            incident = uow.incidents.get(incident_id)
            if incident is None:
                raise NotFoundError(f"incident not found: {incident_id}")
            service = uow.services.get(incident.service_id)
            if service is None:
                raise NotFoundError(
                    f"service not found: {incident.service_id}"
                )
            now = _now(self._clock)
            pack, summary = self._assemble(uow, incident=incident, now=now)
            investigation_id = (
                "inv_"
                + hashlib.sha256(
                    f"{incident_id}|{idempotency_key}".encode()
                ).hexdigest()[:24]
                if idempotency_key
                else self._ids.new("inv")
            )
            investigation = Investigation(
                investigation_id=investigation_id,
                incident_id=incident_id,
                status=InvestigationStatus.COMPLETED,
                result=summary,
                context_pack=pack.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                created_at=now,
                finished_at=now,
            )
            uow.investigations.put(investigation)
            updated, event = record_action(
                incident,
                kind="investigation.started",
                note=note or f"investigation {investigation_id} assembled",
                at=now,
                actor=actor,
            )
            uow.incidents.put(updated, expected_revision=incident.revision)
            uow.incident_events.append(event)
            uow.audit.append(
                AuditEvent(
                    audit_id=self._ids.new("aud"),
                    at=now,
                    actor=actor,
                    action="investigation.start",
                    target_type="incident",
                    target_id=incident_id,
                    details={"investigation_id": investigation_id},
                )
            )
            uow.commit()
            return investigation, False

    def _assemble(
        self, uow: UnitOfWork, *, incident: Incident, now: datetime
    ) -> tuple[InvestigationPack, dict[str, Any]]:
        truncated: list[str] = []
        events = uow.incident_events.list(incident.incident_id)
        if len(events) > self.EVENT_CAP:
            events = events[-self.EVENT_CAP :]
            truncated.append("events")
        anomalies = uow.anomalies.list_for_incident(incident.incident_id)
        if len(anomalies) > self.ANOMALY_CAP:
            anomalies = anomalies[-self.ANOMALY_CAP :]
            truncated.append("anomalies")
        refs = sorted(
            set(incident.evidence_refs)
            | {r for a in anomalies for r in a.evidence_refs}
        )
        if len(refs) > self.EVIDENCE_CAP:
            refs = refs[: self.EVIDENCE_CAP]
            truncated.append("evidence")
        evidence, unresolved = _resolve_evidence(uow, refs)

        health, _ = _service_health_coverage(
            uow, incident.service_id, now=now
        )
        edges = uow.dependency_observations.list_for_service(
            incident.service_id, window_start=incident.first_observed_at
        )[: self.EDGE_CAP]

        unknowns: list[str] = [
            f"evidence_unresolved:{r}" for r in unresolved
        ]
        structural, structural_edges = self._structural_view(uow, incident)
        if structural.state != "AVAILABLE":
            unknowns.append(f"structural_context:{structural.reason}")

        observed_tuples = [
            (e.service_id, e.target,
             f"{e.calls} calls/{e.errors} errors/{e.timeouts} timeouts")
            for e in edges
        ]
        suspected = _suspected_origin(edges)
        correlation: CorrelationReport = correlate_edges(
            service_id=incident.service_id,
            anomaly_ids=[a.anomaly_id for a in anomalies],
            observed=observed_tuples,
            structural=structural_edges,
            suspected=suspected,
            unknowns=[],
            evidence_refs=refs,
            incident_id=incident.incident_id,
        )
        pack = InvestigationPack(
            incident=incident,
            events=events,
            anomalies=anomalies,
            evidence=evidence,
            evidence_unresolved=unresolved,
            indicators=health,
            correlation=correlation,
            structural_context=structural,
            truncated=truncated,
            unknowns=sorted(unknowns),
            limitations=[
                f"bounded context pack — caps: events<={self.EVENT_CAP}, "
                f"anomalies<={self.ANOMALY_CAP}, evidence<={self.EVIDENCE_CAP}",
                "hypotheses are HYPOTHESIZED at best — temporal proximity "
                "is never root cause (LAB-003)",
            ],
        )
        summary = {
            "incident_id": incident.incident_id,
            "title": incident.title,
            "status": incident.status.value,
            "severity": incident.severity.value,
            "anomaly_ids": [a.anomaly_id for a in anomalies],
            "evidence_refs": refs,
            "suspected_origin": suspected,
            "correlation_status": correlation.status.value,
            "structural_context": structural.state,
            "counts": {
                "events": len(events),
                "anomalies": len(anomalies),
                "evidence": len(evidence),
                "evidence_unresolved": len(unresolved),
            },
        }
        return pack, summary

    def _structural_view(
        self, uow: UnitOfWork, incident: Incident
    ) -> tuple[StructuralContextView, list[tuple[str, str, str]]]:
        """Structural slice — AVAILABLE only on a resolved identity + data."""
        if self._structural is None:
            return StructuralContextView(
                state="UNAVAILABLE",
                reason=self._structural_reason
                or "no structural context configured — structural graph "
                "unavailable",
            ), []
        service = uow.services.get(incident.service_id)
        if service is None:
            return StructuralContextView(
                state="UNAVAILABLE", reason="service row missing"
            ), []
        ref = self._structural.resolve_service(service)
        if ref is None:
            return StructuralContextView(
                state="UNRESOLVED",
                reason="no resolvable code_atlas_identity external_ref — "
                "structural identity unresolved (ADR-012); no name-matched "
                "guess made",
            ), []
        dependencies = self._structural.dependencies(ref.atlas_id)
        dependents = self._structural.dependents(ref.atlas_id)
        edge_tuples = [
            (e.from_ref, e.to_ref,
             f"{e.kind} ({e.resolver or e.confidence})")
            for e in dependencies
        ]
        return (
            StructuralContextView(
                state="AVAILABLE",
                reason=f"resolved via code_atlas_identity → {ref.atlas_id}",
                atlas_ref=ref,
                dependencies=dependencies,
                dependents=dependents,
            ),
            edge_tuples,
        )


def _suspected_origin(edges: list[DependencyObservation]) -> str | None:
    """The observed edge with most errors/timeouts — same rule as
    `Correlate._suspected_origin` (LAB-003): a named dependency, never a
    causal claim."""
    if not edges:
        return None
    worst = max(
        edges, key=lambda e: (e.errors + e.timeouts, e.calls, e.target)
    )
    if worst.errors + worst.timeouts == 0:
        return None
    return worst.target


class GetInvestigation:
    """`GET /v1/investigations/{id}` — the persisted pack, verbatim."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, *, investigation_id: str) -> Investigation:
        with self._uow as uow:
            row = uow.investigations.get(investigation_id)
            if row is None:
                raise NotFoundError(
                    f"investigation not found: {investigation_id}"
                )
            return row


class StartIngestionJob:
    """`POST /v1/ingestion/jobs` — bounded synchronous import, audited.

    The pipeline runs inside the request (bounded): the job row records
    queued → running → completed|failed with the honest counters. A re-post
    with the same idempotency key (explicit, or derived from
    source+file+content) returns the same job — never a duplicate import.
    `file_ref` is canonicalized; UNC paths are refused; reads are audited.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        ids: IdFactory,
        redactor: Callable[[str], str],
        *,
        workspace_root: Path | None = None,
        reader: Callable[[Path], bytes] | None = None,
    ) -> None:
        self._uow, self._clock, self._ids = uow, clock, ids
        self._redactor = redactor
        self._ws_root = workspace_root
        self._reader = reader or (lambda p: p.read_bytes())

    def execute(
        self,
        *,
        source_id: str,
        file_ref: str,
        kind: str | None = None,
        idempotency_key: str | None = None,
        actor: str = "api",
    ) -> tuple[IngestionJob, bool]:
        """Returns (job, replayed)."""
        with self._uow as uow:
            source = uow.sources.get(source_id)
            if source is None:
                raise NotFoundError(
                    f"telemetry source not found: {source_id}"
                )
            if not source.enabled:
                raise ValidationError(
                    f"telemetry source is disabled: {source_id}"
                )
            if idempotency_key:
                existing = uow.ingestion_jobs.get_by_idempotency_key(
                    idempotency_key
                )
                if existing is not None:
                    return existing, True

        path = self._resolve(file_ref)
        try:
            content = self._reader(path)
        except OSError as exc:
            raise ValidationError(
                f"cannot read batch file: {file_ref!r}",
                details={"file_ref": file_ref},
            ) from exc
        key = idempotency_key or (
            "sha256:"
            + hashlib.sha256(
                b"|".join((source_id.encode(), str(path).encode(), content))
            ).hexdigest()
        )

        with self._uow as uow:
            existing = uow.ingestion_jobs.get_by_idempotency_key(key)
            if existing is not None:
                return existing, True
            job = IngestionJob(
                job_id=self._ids.new("job"),
                source_id=source_id,
                file_ref=str(path),
                kind=kind,
                status=IngestionJobStatus.RUNNING,
                idempotency_key=key,
                created_at=_now(self._clock),
            )
            uow.ingestion_jobs.put(job)
            uow.commit()

        try:
            result = IngestionPipeline(
                self._uow, self._clock, self._ids, self._redactor
            ).ingest(source=source, kind=kind, content=content)
        except Exception as exc:
            message = getattr(exc, "message", str(exc))
            self._finish(
                job, status=IngestionJobStatus.FAILED,
                error=self._redactor(message)[:500], actor=actor,
            )
            raise

        return self._finish(
            job,
            status=IngestionJobStatus.COMPLETED,
            batch_id=result.batch_id,
            accepted=result.accepted,
            quarantined=result.quarantined,
            skipped_duplicates=result.skipped_duplicates,
            actor=actor,
        ), False

    def _resolve(self, file_ref: str) -> Path:
        if file_ref.startswith(("\\\\", "//")):
            raise ValidationError(
                "UNC paths are refused for ingestion file_ref",
                details={"file_ref": file_ref},
            )
        raw = Path(file_ref).expanduser()
        if not raw.is_absolute():
            if self._ws_root is None:
                raise ValidationError(
                    "relative file_ref requires a workspace root",
                    details={"file_ref": file_ref},
                )
            raw = self._ws_root / raw
        path = raw.resolve()
        if not path.is_file():
            raise ValidationError(
                f"file_ref is not a readable file: {file_ref!r}",
                details={"file_ref": file_ref},
            )
        return path

    def _finish(
        self,
        job: IngestionJob,
        *,
        status: IngestionJobStatus,
        actor: str,
        error: str | None = None,
        batch_id: str | None = None,
        accepted: int = 0,
        quarantined: int = 0,
        skipped_duplicates: int = 0,
    ) -> IngestionJob:
        finished = job.model_copy(
            update={
                "status": status,
                "error": error,
                "batch_id": batch_id,
                "accepted": accepted,
                "quarantined": quarantined,
                "skipped_duplicates": skipped_duplicates,
                "finished_at": _now(self._clock),
            }
        )
        with self._uow as uow:
            uow.ingestion_jobs.put(finished)
            uow.audit.append(
                AuditEvent(
                    audit_id=self._ids.new("aud"),
                    at=_now(self._clock),
                    actor=actor,
                    action=f"ingestion.job.{status.value.lower()}",
                    target_type="ingestion_job",
                    target_id=job.job_id,
                    details={
                        "source_id": job.source_id,
                        "file_ref": job.file_ref,
                        **({"error": error} if error else {}),
                    },
                )
            )
            uow.commit()
        return finished


class GetIngestionJob:
    """`GET /v1/ingestion/jobs/{id}` — the audited job row."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, *, job_id: str) -> IngestionJob:
        with self._uow as uow:
            job = uow.ingestion_jobs.get(job_id)
            if job is None:
                raise NotFoundError(f"ingestion job not found: {job_id}")
            return job
