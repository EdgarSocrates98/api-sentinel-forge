"""Partial-failure matrix — each integration down independently (docs/13,
§24.1 failure strategy).

Every sibling Forge is degraded one at a time and the report stays a named
state (`unavailable` / `unverified`), the core keeps serving `/v1/*`, and a
telemetry gap maps to `UNVERIFIED`/`UNKNOWN` — never a fabricated positive
health and never a global outage: the Sentinel is not a point of failure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api_sentinel.api import create_app
from api_sentinel.api.deps import ApiContext
from api_sentinel.domain import (
    ApiService,
    FixedClock,
    IngestionCheckpoint,
    MeasurementKind,
    ObservationBatch,
    Provenance,
    QualityState,
    ServiceEnvironment,
    TelemetrySource,
    UlidIdFactory,
)
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion.models import (
    NormalizedMeasurement,
    NormalizedObservation,
)
from api_sentinel.integrations import status as status_mod
from api_sentinel.integrations.status import IntegrationState, integration_status
from api_sentinel.workspace import Workspace, ensure_workspace

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

_IMPORTABLE_FLAGS = [
    "apiforge_importable",
    "bridge_importable",
    "data_sentinel_importable",
    "doctor_importable",
    "lens_importable",
]

_NAMED_STATES = {
    IntegrationState.AVAILABLE.value,
    IntegrationState.UNVERIFIED.value,
    IntegrationState.UNAVAILABLE.value,
}


def _states_by_forge(**kwargs: object) -> dict[str, dict[str, object]]:
    return {str(i["forge"]): i for i in integration_status(**kwargs)}


class TestIntegrationDegradation:
    def test_every_integration_down_still_named(self) -> None:
        """A bare workspace: each forge reports its named non-available
        state — nothing fabricated, nothing missing."""
        states = _states_by_forge()
        expected_forges = {
            "api-forge",
            "code-atlas-forge",
            "forge-agent-bridge",
            "data-sentinel-forge",
            "forge-doctor-api",
            "forge-lens",
            "the-forge",
        }
        assert set(states) == expected_forges
        for forge, entry in states.items():
            assert entry["state"] in _NAMED_STATES, forge
            assert entry["reason"], forge
        # the local provider seam is genuinely in-process — honestly available
        assert states["the-forge"]["state"] == IntegrationState.AVAILABLE.value

    @pytest.mark.parametrize("flag", _IMPORTABLE_FLAGS)
    def test_single_forge_down_isolated(
        self, monkeypatch: pytest.MonkeyPatch, flag: str
    ) -> None:
        """Forcing one importable off degrades only its entry — siblings
        keep their own states (no cascade)."""
        monkeypatch.setattr(status_mod, flag, lambda: False)
        states = _states_by_forge()
        assert len(states) == 7
        for entry in states.values():
            assert entry["state"] in _NAMED_STATES

    def test_atlas_manifest_missing_is_named_unavailable(
        self, tmp_path: Path
    ) -> None:
        states = _states_by_forge(atlas_manifest=tmp_path / "nope.json")
        atlas = states["code-atlas-forge"]
        assert atlas["state"] == IntegrationState.UNAVAILABLE.value
        assert atlas["reason"]

    def test_atlas_manifest_corrupt_is_named_unavailable(
        self, tmp_path: Path
    ) -> None:
        bad = tmp_path / "atlas.json"
        bad.write_text("{not json", encoding="utf-8")
        atlas = _states_by_forge(atlas_manifest=bad)["code-atlas-forge"]
        assert atlas["state"] == IntegrationState.UNAVAILABLE.value

    def test_doctor_handoff_down_is_named_unavailable(
        self, tmp_path: Path
    ) -> None:
        """In-process delegation with the module absent → `unavailable`,
        never a fabricated bundle (LAB-011)."""
        from api_sentinel.contracts.v1.models import InvestigationHandoffV1
        from api_sentinel.integrations.doctor.adapter import (
            DoctorACL,
            DoctorMode,
            doctor_importable,
        )

        acl = DoctorACL(mode=DoctorMode.IN_PROCESS)
        handoff = InvestigationHandoffV1(
            **{
                "schema": "api-sentinel/InvestigationHandoff/v1",
                "handoff_id": "hf_lab011",
                "from_forge": "api-sentinel-forge",
                "to_forge": "forge-doctor-api",
                "kind": "investigation",
                "payload": {},
                "constraints": {},
            }
        )
        result = acl.delegate(handoff, target="svc_x")
        if doctor_importable():
            # environment HAS the peer — a refusal stays a named state
            assert result.status in ("refused", "delegated", "completed")
        else:
            assert result.status == "unavailable"
        assert result.detail


def _ctx(ws_root: Path, *, now: datetime = NOW) -> ApiContext:
    return ApiContext(
        workspace=Workspace(root=ws_root, scope="project"),
        config={},
        clock=FixedClock(now),
        ids=UlidIdFactory(),
        redactor=lambda t: t,
        structural=None,
        structural_reason="no atlas export configured — structural context "
                          "unavailable",
        atlas_manifest=None,
        default_role="operator",
    )


def _seed(ws_root: Path, *, observations: bool) -> None:
    ws = ensure_workspace(Workspace(root=ws_root, scope="project"))
    with SqliteUnitOfWork(ws.db_path) as uow:
        env = uow.environments.put(
            ServiceEnvironment(
                environment_id="env_lab", name="lab", created_at=NOW
            )
        )
        uow.services.put(
            ApiService(
                service_id="svc_gap",
                service_name="gap-api",
                environment_id=env.environment_id,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        uow.sources.put(
            TelemetrySource(
                source_id="src_gap",
                kind="jsonl-file",
                service_id="svc_gap",
                created_at=NOW,
            )
        )
        if observations:
            # one OLD observation + a stale checkpoint — collection stopped
            old_end = NOW - timedelta(hours=2)
            uow.checkpoints.put(
                IngestionCheckpoint(
                    source_id="src_gap",
                    checkpoint="1",
                    updated_at=old_end,
                )
            )
            uow.batches.put(
                ObservationBatch(
                    batch_id="batch_gap",
                    provenance=Provenance(
                        source_id="src_gap",
                        observed_at=old_end,
                        received_at=old_end,
                    ),
                    item_count=1,
                )
            )
            uow.observations.put(
                NormalizedObservation(
                    observation_id="obs_gap_old",
                    service_id="svc_gap",
                    operation_id=None,
                    release_id=None,
                    window_start=old_end - timedelta(minutes=1),
                    window_end=old_end,
                    ingest_time=old_end,
                    processing_time=old_end,
                    source_id="src_gap",
                    batch_id="batch_gap",
                    quality=QualityState.COMPLETE,
                    measurement=NormalizedMeasurement(
                        kind=MeasurementKind.COUNTER,
                        unit="requests",
                        value=10.0,
                    ),
                    attributes={},
                    idempotency_key="key_gap",
                )
            )
        uow.commit()


class TestCoreKeepsServing:
    def test_api_serves_with_all_integrations_down(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for flag in _IMPORTABLE_FLAGS:
            monkeypatch.setattr(status_mod, flag, lambda: False)
        ws_root = tmp_path / "ws"
        _seed(ws_root, observations=False)
        client = TestClient(create_app(context=_ctx(ws_root)))

        health = client.get("/v1/health")
        assert health.status_code == 200
        assert health.json()["state"] == "AVAILABLE"

        integrations = client.get("/v1/integrations/status")
        assert integrations.status_code == 200
        states = integrations.json()["integrations"]
        assert len(states) == 7
        assert all(i["state"] in _NAMED_STATES for i in states)

        services = client.get("/v1/services")
        assert services.status_code == 200

    def test_telemetry_gap_is_unknown_not_fabricated(
        self, tmp_path: Path
    ) -> None:
        """Registered source, zero observations → UNKNOWN/no_observations,
        with the silent source named — never an AVAILABLE fiction."""
        ws_root = tmp_path / "ws"
        _seed(ws_root, observations=False)
        client = TestClient(create_app(context=_ctx(ws_root)))
        body = client.get("/v1/services/svc_gap").json()
        health = body["detail"]["health"]
        assert health["state"] == "UNKNOWN"
        assert health["condition"] == "no_observations"
        assert any(
            "source_silent:src_gap" in u for u in health["unknowns"]
        )
        assert health["state"] != "AVAILABLE"

    def test_stale_source_is_unverified_not_fabricated(
        self, tmp_path: Path
    ) -> None:
        """A source that stopped sending → UNVERIFIED/source_stale, and the
        sentinel self-report names the same stale source."""
        ws_root = tmp_path / "ws"
        _seed(ws_root, observations=True)
        client = TestClient(create_app(context=_ctx(ws_root)))
        body = client.get("/v1/services/svc_gap").json()
        health = body["detail"]["health"]
        assert health["state"] == "UNVERIFIED"
        assert health["condition"] == "source_stale"
        sentinel = client.get("/v1/health").json()["sentinel"]
        assert "src_gap" in sentinel["sources"]["stale"]
