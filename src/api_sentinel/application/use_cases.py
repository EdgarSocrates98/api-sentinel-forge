"""Use cases — orchestrate ports; deterministic via injected clock/ids."""

from __future__ import annotations

import hashlib
from datetime import datetime

from api_sentinel.application.ports import UnitOfWork
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
