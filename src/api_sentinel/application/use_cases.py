"""Use cases — orchestrate ports; deterministic via injected clock/ids."""

from __future__ import annotations

import hashlib
from datetime import datetime

from api_sentinel.application.ports import UnitOfWork
from api_sentinel.domain import (
    ApiService,
    AuditEvent,
    Clock,
    EvidenceArtifact,
    IdFactory,
    NotFoundError,
    ServiceEnvironment,
    TelemetrySource,
    ValidationError,
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
