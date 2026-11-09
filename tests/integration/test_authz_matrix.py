"""LAB-016 — authorization matrix over the capability scope model.

The Cycle-6 placeholder seam (`X-Sentinel-Role` → docs/07 role scopes) is
exercised end-to-end: every gated action is tried under every role, and a
denial is asserted to (a) return the typed `sentinel.forbidden` problem,
(b) happen *before* resource lookup — a denied call against a nonexistent
target is indistinguishable from one against an existing target (no
existence leakage) — and (c) leave an `api.scope.denied` audit row.

Read routes (`/v1/services`, `/v1/incidents`, …) are intentionally ungated
in the placeholder model: single-user localhost operator. The matrix
documents that explicitly rather than pretending a stronger model exists.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api_sentinel.api import create_app
from api_sentinel.api.deps import ApiContext
from api_sentinel.detection import Severity
from api_sentinel.domain import (
    ApiService,
    EvidenceArtifact,
    FixedClock,
    ServiceEnvironment,
    TelemetrySource,
    UlidIdFactory,
)
from api_sentinel.incidents import Incident, IncidentStatus
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.workspace import Workspace, ensure_workspace

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
INCIDENT = "inc_lab016"
EVIDENCE = "ev_lab016"
SOURCE = "src_lab016"


def _seed(ws_root: Path) -> Path:
    ws = ensure_workspace(Workspace(root=ws_root, scope="project"))
    telemetry = ws_root / "telemetry.jsonl"
    telemetry.write_text(
        '{"schema": "api-sentinel/RuntimeObservation/v1",'
        ' "observation_id": "obs_lab016", "service_id": "svc_lab016",'
        ' "window": {"start": "2026-01-01T11:58:00Z",'
        ' "end": "2026-01-01T11:59:00Z"},'
        f' "source_id": "{SOURCE}", "quality": "COMPLETE",'
        ' "measurement": {"kind": "counter", "unit": "requests", "value": 1},'
        ' "attributes": {}}\n',
        encoding="utf-8",
    )
    with SqliteUnitOfWork(ws.db_path) as uow:
        env = uow.environments.put(
            ServiceEnvironment(
                environment_id="env_lab", name="lab", created_at=NOW
            )
        )
        uow.services.put(
            ApiService(
                service_id="svc_lab016",
                service_name="payments-api",
                environment_id=env.environment_id,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        uow.sources.put(
            TelemetrySource(
                source_id=SOURCE,
                kind="jsonl-file",
                service_id="svc_lab016",
                created_at=NOW,
            )
        )
        uow.evidence.put(
            EvidenceArtifact(
                evidence_id=EVIDENCE,
                kind="metric-export",
                created_at=NOW,
                hash="sha256:" + "b" * 64,
                size_bytes=8,
                source_id=SOURCE,
            )
        )
        uow.incidents.put(
            Incident(
                incident_id=INCIDENT,
                title="lab016 incident",
                severity=Severity.SEV3,
                status=IncidentStatus.DETECTED,
                service_id="svc_lab016",
                group_key="svc_lab016|service|availability",
                first_observed_at=NOW,
                detected_at=NOW,
            )
        )
        uow.commit()
    return telemetry


def _ctx(ws_root: Path) -> ApiContext:
    return ApiContext(
        workspace=Workspace(root=ws_root, scope="project"),
        config={},
        clock=FixedClock(NOW),
        ids=UlidIdFactory(),
        redactor=lambda t: t,
        structural=None,
        structural_reason="no atlas export configured — structural context "
                          "unavailable",
        atlas_manifest=None,
        default_role="operator",
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    ws_root = tmp_path / "ws"
    _seed(ws_root)
    return TestClient(create_app(context=_ctx(ws_root)))


def _denied_audit_rows(ws_root: Path) -> list[dict[str, object]]:
    conn = sqlite3.connect(str(ws_root / "sentinel.db"))
    try:
        return [
            {"target_id": r[0], "details": json.loads(r[1])}
            for r in conn.execute(
                "SELECT target_id, details FROM audit_events"
                " WHERE action='api.scope.denied'"
            )
        ]
    finally:
        conn.close()


# (endpoint, allowed roles) — POST /v1/ingestion/jobs needs a real file
# (seeded telemetry.jsonl), POST investigations needs the seeded incident.
_MATRIX = [
    # scope "query" — every declared role
    ("GET", f"/v1/evidence/{EVIDENCE}", None, {"viewer", "analyst", "operator", "admin"}),
    # scope "import"
    (
        "POST",
        "/v1/ingestion/jobs",
        {"source_id": SOURCE, "file_ref": "telemetry.jsonl"},
        {"operator", "admin"},
    ),
    # scope "investigate"
    (
        "POST",
        f"/v1/incidents/{INCIDENT}/investigations",
        {"note": "lab016"},
        {"analyst", "operator", "admin"},
    ),
]

ROLES = ["viewer", "analyst", "operator", "admin", "intruder", ""]


class TestAuthorizationMatrix:
    @pytest.mark.parametrize("role", ROLES)
    def test_matrix(self, client: TestClient, tmp_path: Path, role: str) -> None:
        ws_root = tmp_path / "ws"
        headers = {"X-Sentinel-Role": role} if role else {}
        expected_default = "operator"  # no header → workspace default
        effective = role or expected_default
        for method, path, body, allowed in _MATRIX:
            if method == "GET":
                resp = client.get(path, headers=headers)
            else:
                resp = client.post(path, json=body or {}, headers=headers)
            should_allow = effective in allowed
            if should_allow:
                assert resp.status_code in (200, 201), (
                    f"{effective} {method} {path} → {resp.status_code}: {resp.text}"
                )
            else:
                assert resp.status_code == 403, (
                    f"{effective} {method} {path} → {resp.status_code}: {resp.text}"
                )
                problem = resp.json()
                assert problem["code"] == "sentinel.forbidden"
                assert problem["status"] == 403
        denied = _denied_audit_rows(ws_root)
        # every denial this test produced is auditable
        expected_denials = sum(
            1 for _m, _p, _b, allowed in _MATRIX if effective not in allowed
        )
        assert len(denied) >= expected_denials
        roles_seen = {d["details"].get("role") for d in denied}
        assert effective in roles_seen or expected_denials == 0

    def test_denial_never_leaks_existence(self, client: TestClient) -> None:
        """Viewer denied on a real incident and a ghost incident: identical."""
        headers = {"X-Sentinel-Role": "viewer"}
        real = client.post(
            f"/v1/incidents/{INCIDENT}/investigations",
            json={},
            headers=headers,
        )
        ghost = client.post(
            "/v1/incidents/inc_ghost/investigations",
            json={},
            headers=headers,
        )
        assert real.status_code == ghost.status_code == 403
        real_body, ghost_body = real.json(), ghost.json()
        assert real_body["code"] == ghost_body["code"] == "sentinel.forbidden"
        # neither body names the incident nor hints whether it exists
        for body in (real_body, ghost_body):
            text = str(body).lower()
            assert INCIDENT.lower() not in text
            assert "ghost" not in text
            assert "not_found" not in text
            assert "not found" not in text

    def test_reads_are_the_documented_placeholder(self, client: TestClient) -> None:
        """Ungated reads are the placeholder model — asserted, not assumed."""
        for role in ("viewer", "intruder"):
            resp = client.get("/v1/services", headers={"X-Sentinel-Role": role})
            assert resp.status_code == 200
