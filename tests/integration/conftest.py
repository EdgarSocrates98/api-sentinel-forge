"""Shared fixtures for ingestion integration tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from api_sentinel.domain import ApiService, ServiceEnvironment, TelemetrySource
from api_sentinel.infrastructure.storage import SqliteUnitOfWork

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sentinel.db"


def seed_service_and_source(
    uow: SqliteUnitOfWork,
    *,
    service_id: str = "svc_lab_payments",
    service_name: str = "payments-api",
    source_id: str = "src_lab_jsonl",
    source_kind: str = "jsonl-file",
) -> TelemetrySource:
    env = uow.environments.get_by_name("lab") or uow.environments.put(
        ServiceEnvironment(environment_id="env_lab", name="lab", created_at=NOW)
    )
    uow.services.put(
        ApiService(
            service_id=service_id,
            service_name=service_name,
            environment_id=env.environment_id,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    return uow.sources.put(
        TelemetrySource(
            source_id=source_id,
            kind=source_kind,
            service_id=service_id,
            created_at=NOW,
        )
    )
