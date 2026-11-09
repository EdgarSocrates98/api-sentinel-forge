"""Cycle-6 API surface integration tests — the docs/04 route table end to end.

Every test exercises the real HTTP surface (FastAPI TestClient) over a seeded
workspace: use cases, repositories and migrations are production code — the
routers stay thin. The seed itself runs through the real ingestion pipeline
so metrics/health reflect genuinely ingested series, never fixtures hand-fed
into response shapes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api_sentinel.api import create_app
from api_sentinel.api.deps import ApiContext
from api_sentinel.application.use_cases import DefineSlo, EvaluateSlo
from api_sentinel.detection import (
    Anomaly,
    AnomalyStatus,
    Explanation,
    RuleRef,
    Severity,
)
from api_sentinel.domain import (
    ApiOperation,
    ApiService,
    EvidenceArtifact,
    ExternalRef,
    FixedClock,
    OperationIdentity,
    SequentialIdFactory,
    ServiceEnvironment,
    TelemetrySource,
    UlidIdFactory,
)
from api_sentinel.incidents import (
    EventKind,
    Incident,
    IncidentStatus,
    make_event,
)
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion import IngestionPipeline
from api_sentinel.integrations.service import RecordDependency
from api_sentinel.workspace import Workspace, ensure_workspace

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
W1S = datetime(2026, 1, 1, 11, 58, tzinfo=UTC)
W1E = datetime(2026, 1, 1, 11, 59, tzinfo=UTC)
W2S = datetime(2026, 1, 1, 11, 59, tzinfo=UTC)
W2E = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

SERVICE = "svc_payments"
SOURCE = "src_lab_jsonl"


def _obs_line(
    obs_id: str,
    op_id: str | None,
    status: int,
    ws: str,
    we: str,
    count: int,
) -> dict[str, object]:
    """One RuntimeObservationV1 line — per-window histogram + status class."""
    return {
        "schema": "api-sentinel/RuntimeObservation/v1",
        "observation_id": obs_id,
        "service_id": SERVICE,
        "operation_id": op_id,
        "release_id": None,
        "window": {"start": ws, "end": we},
        "source_id": SOURCE,
        "quality": "COMPLETE",
        "measurement": {
            "kind": "histogram",
            "unit": "ms",
            "count": count,
            "sum": float(count * 100),
            "buckets": [
                {"le": 50, "count": count // 2},
                {"le": 200, "count": count - count // 4},
                {"le": "+Inf", "count": count},
            ],
        },
        "attributes": {
            "http.route": "/payments/{id}",
            "http.request.method": "GET",
            "http.response.status_code": status,
        },
    }


def _seed_workspace(ws_root: Path) -> None:
    """Service + source + two windows of real series + SLO + incident +
    evidence + declared op + observed edge — all through real code paths."""
    ensure_workspace(Workspace(root=ws_root, scope="project"))
    db = ws_root / "sentinel.db"
    ids = SequentialIdFactory()
    clock = FixedClock(NOW)
    with SqliteUnitOfWork(db) as uow:
        env = uow.environments.put(
            ServiceEnvironment(
                environment_id="env_lab", name="lab", created_at=NOW
            )
        )
        uow.services.put(
            ApiService(
                service_id=SERVICE,
                service_name="payments-api",
                environment_id=env.environment_id,
                external_refs=[
                    ExternalRef(
                        kind="pf_api",
                        ref="pf:api:payments",
                        provenance="verified",
                    )
                ],
                created_at=NOW,
                updated_at=NOW,
            )
        )
        source = uow.sources.put(
            TelemetrySource(
                source_id=SOURCE,
                kind="jsonl-file",
                service_id=SERVICE,
                created_at=NOW,
            )
        )
        uow.operations.put(
            ApiOperation.make(
                operation_id="op_post_payments",
                service_id=SERVICE,
                identity=OperationIdentity(
                    protocol="http", method="POST", route="/payments"
                ),
                created_at=NOW,
            )
        )
        uow.commit()

    lines = [
        _obs_line("obs_w1_ok", "op_get_payments", 200,
                  "2026-01-01T11:58:00Z", "2026-01-01T11:59:00Z", 18),
        _obs_line("obs_w1_err", "op_get_payments", 503,
                  "2026-01-01T11:58:00Z", "2026-01-01T11:59:00Z", 2),
        _obs_line("obs_w2_ok", "op_get_payments", 200,
                  "2026-01-01T11:59:00Z", "2026-01-01T12:00:00Z", 20),
    ]
    content = "\n".join(json.dumps(o) for o in lines).encode()
    IngestionPipeline(SqliteUnitOfWork(db), clock, ids, lambda t: t).ingest(
        source=source, kind="jsonl", content=content
    )

    DefineSlo(SqliteUnitOfWork(db), clock, ids).execute(
        service_id=SERVICE, target=0.99, window_seconds=120,
        slo_id="slo_availability",
    )
    EvaluateSlo(SqliteUnitOfWork(db), clock, ids).execute(
        slo_id="slo_availability", window_start=W1S, window_end=W2E
    )

    RecordDependency(SqliteUnitOfWork(db), clock).execute(
        service_id=SERVICE,
        target="payments-db",
        target_kind="datastore",
        window_start=W1S,
        window_end=W1E,
        calls=20,
        errors=2,
        timeouts=1,
        evidence_refs=["ev_metric_w1"],
    )

    with SqliteUnitOfWork(db) as uow:
        uow.evidence.put(
            EvidenceArtifact(
                evidence_id="ev_metric_w1",
                kind="metric-export",
                created_at=NOW,
                hash="sha256:" + "a" * 64,
                size_bytes=512,
                source_id=SOURCE,
                blob_ref="sha256:" + "a" * 64,
            )
        )
        uow.incidents.put(
            Incident(
                incident_id="inc_1",
                title="elevated error ratio on GET /payments/{id}",
                severity=Severity.SEV3,
                status=IncidentStatus.INVESTIGATING,
                service_id=SERVICE,
                operation_id="op_get_payments",
                operation_ref="GET /payments/{id}",
                group_key=f"{SERVICE}|op_get_payments|availability",
                first_observed_at=W1S,
                detected_at=W1E,
                anomaly_ids=["anm_1"],
                evidence_refs=["ev_metric_w1"],
            )
        )
        uow.anomalies.put(
            Anomaly(
                anomaly_id="anm_1",
                service_id=SERVICE,
                operation_id="op_get_payments",
                operation_ref="GET /payments/{id}",
                detected_at=W1E,
                window_start=W1S,
                window_end=W1E,
                rule=RuleRef(
                    rule_id="rule_availability_burn",
                    rule_revision=1,
                    detector_id="availability",
                    detector_version="1",
                ),
                severity=Severity.SEV3,
                status=AnomalyStatus.OBSERVED,
                explanation=Explanation(
                    summary="error ratio 0.10 over window W1"
                ),
                evidence_refs=["ev_metric_w1"],
                dedup_key="op_get_payments",
                incident_id="inc_1",
            )
        )
        uow.incident_events.append(
            make_event(
                incident_id="inc_1",
                kind=EventKind.OPENED,
                at=W1E,
                actor="system",
                to_status=IncidentStatus.DETECTED,
                note="opened by detection",
            )
        )
        uow.commit()


def _ctx(ws_root: Path, *, now: datetime = NOW) -> ApiContext:
    return ApiContext(
        workspace=Workspace(root=ws_root, scope="project"),
        config={},
        clock=FixedClock(now),
        ids=UlidIdFactory(),  # fresh ids — sequential factories collide
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
    _seed_workspace(ws_root)
    return TestClient(create_app(context=_ctx(ws_root)))


def _problem(resp: object) -> dict[str, object]:
    body = resp.json()  # type: ignore[attr-defined]
    assert resp.headers["content-type"].startswith(  # type: ignore[attr-defined]
        "application/problem+json"
    )
    assert body["type"].startswith("urn:api-sentinel:")
    assert body["status"] == resp.status_code  # type: ignore[attr-defined]
    return body


class TestSentinelEndpoints:
    def test_health_reports_real_state(self, client: TestClient) -> None:
        body = client.get("/v1/health").json()
        assert body["state"] == "AVAILABLE"
        assert body["database"] == "present"
        assert body["workspace_initialized"] is True
        assert "unknowns" in body and "evidence_refs" in body

    def test_capabilities_portal_operations_available(
        self, client: TestClient
    ) -> None:
        from api_sentinel.integrations.aws.client import prove_read_only

        body = client.get("/v1/capabilities").json()
        caps = {c["capability_id"]: c["status"] for c in body["capabilities"]}
        assert caps["api.portal-operations"] == "available"
        # Cycle 7: AVAILABLE ⇔ the mechanical read-only proof passed.
        passed, _detail = prove_read_only()
        assert caps["api.aws-readonly-observability"] == (
            "available" if passed else "declared"
        )

    def test_integrations_status_named_states(self, client: TestClient) -> None:
        body = client.get("/v1/integrations/status").json()
        states = {i["forge"]: i["state"] for i in body["integrations"]}
        assert states["code-atlas-forge"] == "unavailable"
        assert states["the-forge"] == "available"
        assert "forge-doctor-api" in states


class TestServices:
    def test_service_list_is_real_inventory(self, client: TestClient) -> None:
        body = client.get("/v1/services").json()
        assert len(body["services"]) == 1
        row = body["services"][0]
        assert row["service"]["service_id"] == SERVICE
        assert row["coverage"]["observations"] == 3
        assert row["coverage"]["sources_observed"] == 1
        assert row["health"]["state"] == "AVAILABLE"
        assert row["open_incidents"] == 1
        assert row["slo_definitions"] == 1

    def test_service_detail_health_and_proven_links(
        self, client: TestClient
    ) -> None:
        body = client.get(f"/v1/services/{SERVICE}").json()
        assert body["detail"]["health"]["state"] == "AVAILABLE"
        assert body["detail"]["health"]["condition"] == "observed"
        corr = body["links"]["correspondence"]
        assert corr["state"] == "proven"  # verified-provenance pf_api ref
        assert body["links"]["links"][0]["ref"] == "pf:api:payments"

    def test_unknown_service_is_typed_404(self, client: TestClient) -> None:
        resp = client.get("/v1/services/ghost")
        assert resp.status_code == 404
        body = _problem(resp)
        assert body["code"] == "sentinel.not_found"
        assert "ghost" in body["detail"]

    def test_operations_keep_declared_and_observed_distinct(
        self, client: TestClient
    ) -> None:
        body = client.get(f"/v1/services/{SERVICE}/operations").json()
        ops = {o["operation_id"]: o for o in body["operations"]}
        observed = ops["op_get_payments"]
        assert observed["provenance"] == "observed"
        assert observed["observations"] == 3
        assert observed["operation_ref"] == "GET /payments/{id}"
        declared = ops["op_post_payments"]
        assert declared["provenance"] == "declared"
        assert declared["observations"] == 0

    def test_metrics_carry_units_windows_and_exclusions(
        self, client: TestClient
    ) -> None:
        body = client.get(f"/v1/services/{SERVICE}/metrics").json()
        metrics = body["metrics"]
        assert metrics["units"]["latency"] == "ms"
        assert metrics["units"]["request_rate"] == "requests/second"
        assert len(metrics["windows"]) == 2
        first, second = metrics["windows"]
        assert first["duration_seconds"] == 60.0
        agg1 = first["aggregates"][0]
        assert agg1["eligible_requests"] == 20.0
        assert agg1["error_requests"] == 2.0
        assert agg1["error_ratio"] == pytest.approx(0.1)
        assert second["aggregates"][0]["error_ratio"] == pytest.approx(0.0)
        assert metrics["observations"] == 3
        assert metrics["qualities"]["COMPLETE"] == 3
        assert "gaps" in metrics and "excluded_by_reason" in metrics

    def test_metrics_window_filter_and_operation_filter(
        self, client: TestClient
    ) -> None:
        body = client.get(
            f"/v1/services/{SERVICE}/metrics",
            params={
                "window_start": "2026-01-01T11:59:00Z",
                "operation_id": "op_get_payments",
            },
        ).json()
        assert len(body["metrics"]["windows"]) == 1
        assert body["metrics"]["windows"][0]["window_end"].startswith(
            "2026-01-01T12:00"
        )
        body = client.get(
            f"/v1/services/{SERVICE}/metrics",
            params={"operation_id": "op_unknown"},
        ).json()
        assert body["metrics"]["windows"] == []
        assert "no_observations_for_operation" in body["metrics"]["unknowns"]

    def test_slos_show_revision_pinned_history(
        self, client: TestClient
    ) -> None:
        body = client.get(f"/v1/services/{SERVICE}/slos").json()
        entries = body["panel"]["slos"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["definition"]["slo_id"] == "slo_availability"
        assert entry["definition"]["revision"] == 1
        assert entry["definition"]["target"] == pytest.approx(0.99)
        assert entry["assessments"], "expected windowed history"
        assert entry["latest_assessment"]["revision"] == 1
        assert entry["latest_assessment"]["state"] in (
            "MET",
            "VIOLATED",
            "INSUFFICIENT_DATA",
        )

    def test_stale_source_health_is_unverified_not_fiction(
        self, tmp_path: Path
    ) -> None:
        """A source that stopped reporting never yields positive health."""
        ws_root = tmp_path / "ws"
        _seed_workspace(ws_root)
        stale_clock = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)  # 1h later
        stale_client = TestClient(
            create_app(context=_ctx(ws_root, now=stale_clock))
        )
        body = stale_client.get(f"/v1/services/{SERVICE}").json()
        assert body["detail"]["health"]["state"] == "UNVERIFIED"
        assert body["detail"]["health"]["condition"] == "source_stale"
        assert body["detail"]["health"]["latest_indicators"] is None


class TestIngestionJobs:
    def test_job_roundtrip_idempotency_and_audit(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        ws_root = tmp_path / "ws"
        batch = ws_root / "batch.jsonl"
        line = _obs_line(
            "obs_w3_ok", "op_get_payments", 200,
            "2026-01-01T12:00:00Z", "2026-01-01T12:01:00Z", 20,
        )
        batch.write_text(json.dumps(line) + "\n", encoding="utf-8")

        resp = client.post(
            "/v1/ingestion/jobs",
            json={
                "source_id": SOURCE,
                "file_ref": "batch.jsonl",  # workspace-relative
                "idempotency_key": "ik-import-1",
            },
        )
        assert resp.status_code == 201, resp.text
        job = resp.json()["job"]
        assert job["status"] == "COMPLETED"
        assert job["accepted"] == 1
        assert job["batch_id"]

        replay = client.post(
            "/v1/ingestion/jobs",
            json={
                "source_id": SOURCE,
                "file_ref": "batch.jsonl",
                "idempotency_key": "ik-import-1",
            },
        )
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
        assert replay.json()["job"]["job_id"] == job["job_id"]

        got = client.get(f"/v1/ingestion/jobs/{job['job_id']}").json()
        assert got["job"]["status"] == "COMPLETED"

        with SqliteUnitOfWork(ws_root / "sentinel.db") as uow:
            audits = uow.audit.list(target_id=job["job_id"])
            assert any(
                a.action == "ingestion.job.completed" for a in audits
            )
            assert uow.observations.count() == 4  # 3 seeded + 1 imported

    def test_unknown_source_is_404(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/ingestion/jobs",
            json={"source_id": "ghost", "file_ref": "x.jsonl"},
        )
        assert resp.status_code == 404
        assert _problem(resp)["code"] == "sentinel.not_found"

    def test_missing_fields_are_422_problem(self, client: TestClient) -> None:
        resp = client.post("/v1/ingestion/jobs", json={})
        assert resp.status_code == 422
        assert _problem(resp)["code"] == "sentinel.validation"

    def test_missing_file_is_422(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/ingestion/jobs",
            json={"source_id": SOURCE, "file_ref": "nope.jsonl"},
        )
        assert resp.status_code == 422
        assert _problem(resp)["code"] == "sentinel.validation"

    def test_unknown_job_is_404(self, client: TestClient) -> None:
        resp = client.get("/v1/ingestion/jobs/job_ghost")
        assert _problem(resp)["code"] == "sentinel.not_found"


class TestAnomaliesAndIncidents:
    def test_anomalies_feed(self, client: TestClient) -> None:
        body = client.get(
            "/v1/anomalies", params={"service_id": SERVICE}
        ).json()
        assert [a["anomaly_id"] for a in body["anomalies"]] == ["anm_1"]
        assert body["anomalies"][0]["status"] == "OBSERVED"
        assert body["evidence_refs"] == ["ev_metric_w1"]

    def test_anomaly_status_filter_and_validation(
        self, client: TestClient
    ) -> None:
        body = client.get("/v1/anomalies", params={"status": "verified"}).json()
        assert body["anomalies"] == []
        resp = client.get("/v1/anomalies", params={"status": "bogus"})
        assert resp.status_code == 422
        assert _problem(resp)["code"] == "sentinel.validation"

    def test_incidents_open_and_historical(self, client: TestClient) -> None:
        body = client.get("/v1/incidents", params={"status": "open"}).json()
        assert [i["incident_id"] for i in body["incidents"]] == ["inc_1"]
        body = client.get("/v1/incidents", params={"status": "closed"}).json()
        assert body["incidents"] == []

    def test_incident_detail_carries_evidence_metadata(
        self, client: TestClient
    ) -> None:
        body = client.get("/v1/incidents/inc_1").json()
        detail = body["detail"]
        assert detail["incident"]["status"] == "INVESTIGATING"
        assert [a["anomaly_id"] for a in detail["anomalies"]] == ["anm_1"]
        assert [e["evidence_id"] for e in detail["evidence"]] == [
            "ev_metric_w1"
        ]
        assert detail["evidence_unresolved"] == []
        assert "ev_metric_w1" in body["evidence_refs"]

    def test_incident_timeline_journal_and_edges(
        self, client: TestClient
    ) -> None:
        body = client.get("/v1/incidents/inc_1/timeline").json()
        timeline = body["timeline"]
        assert timeline["events"][0]["kind"] == "opened"
        edges = timeline["dependency_observations"]
        assert edges and edges[0]["target"] == "payments-db"
        assert "ev_metric_w1" in body["evidence_refs"]

    def test_unknown_incident_is_404(self, client: TestClient) -> None:
        resp = client.get("/v1/incidents/inc_ghost")
        assert _problem(resp)["code"] == "sentinel.not_found"


class TestInvestigations:
    def test_context_pack_without_atlas_is_unavailable_not_fabricated(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/v1/incidents/inc_1/investigations",
            json={"idempotency_key": "ik-inv-1", "note": "triage"},
        )
        assert resp.status_code == 201, resp.text
        inv = resp.json()["investigation"]
        assert inv["status"] == "COMPLETED"
        assert inv["incident_id"] == "inc_1"
        pack = inv["context_pack"]
        assert pack["incident"]["incident_id"] == "inc_1"
        assert [a["anomaly_id"] for a in pack["anomalies"]] == ["anm_1"]
        structural = pack["structural_context"]
        assert structural["state"] == "UNAVAILABLE"
        assert structural["dependencies"] == []
        assert any(
            "structural_context" in u for u in pack["unknowns"]
        )
        assert pack["indicators"]["state"] == "AVAILABLE"
        assert pack["correlation"]["suspected_origin"] == "payments-db"

        replay = client.post(
            "/v1/incidents/inc_1/investigations",
            json={"idempotency_key": "ik-inv-1"},
        )
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
        assert replay.json()["investigation"]["investigation_id"] == (
            inv["investigation_id"]
        )

        got = client.get(
            f"/v1/investigations/{inv['investigation_id']}"
        ).json()
        assert got["investigation"]["status"] == "COMPLETED"

    def test_investigation_journal_and_audit(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        client.post("/v1/incidents/inc_1/investigations", json={})
        timeline = client.get("/v1/incidents/inc_1/timeline").json()
        kinds = [e["kind"] for e in timeline["timeline"]["events"]]
        assert "action_recorded" in kinds
        with SqliteUnitOfWork(
            tmp_path / "ws" / "sentinel.db"
        ) as uow:
            audits = uow.audit.list(target_id="inc_1")
            assert any(a.action == "investigation.start" for a in audits)

    def test_investigation_unknown_incident_404(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/v1/incidents/inc_ghost/investigations", json={}
        )
        assert _problem(resp)["code"] == "sentinel.not_found"

    def test_unknown_investigation_404(self, client: TestClient) -> None:
        resp = client.get("/v1/investigations/inv_ghost")
        assert _problem(resp)["code"] == "sentinel.not_found"


class TestEvidence:
    def test_metadata_only_never_blob(self, client: TestClient) -> None:
        body = client.get("/v1/evidence/ev_metric_w1").json()
        evidence = body["evidence"]
        assert evidence["evidence_id"] == "ev_metric_w1"
        assert evidence["hash"].startswith("sha256:")
        assert evidence["kind"] == "metric-export"
        assert set(evidence) <= {
            "evidence_id",
            "kind",
            "created_at",
            "hash",
            "size_bytes",
            "truncated",
            "loss_notes",
            "source_id",
            "blob_ref",
        }
        raw = json.dumps(body)
        assert "BEGIN" not in raw and "blob_content" not in evidence

    def test_evidence_read_is_audited(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        client.get("/v1/evidence/ev_metric_w1")
        with SqliteUnitOfWork(
            tmp_path / "ws" / "sentinel.db"
        ) as uow:
            audits = uow.audit.list(target_id="ev_metric_w1")
            assert any(a.action == "evidence.read" for a in audits)

    def test_unknown_evidence_404(self, client: TestClient) -> None:
        resp = client.get("/v1/evidence/ev_ghost")
        assert _problem(resp)["code"] == "sentinel.not_found"


class TestAuthorizationPlaceholder:
    """LAB-016 groundwork: role→scope denial before lookup, no leakage."""

    def test_viewer_can_query_but_not_mutate(self, client: TestClient) -> None:
        viewer = {"X-Sentinel-Role": "viewer"}
        assert client.get("/v1/services", headers=viewer).status_code == 200
        resp = client.post(
            "/v1/incidents/inc_1/investigations", json={}, headers=viewer
        )
        assert resp.status_code == 403
        assert _problem(resp)["code"] == "sentinel.forbidden"
        resp = client.post(
            "/v1/ingestion/jobs",
            json={"source_id": SOURCE, "file_ref": "x"},
            headers=viewer,
        )
        assert resp.status_code == 403

    def test_analyst_investigates_but_cannot_import(
        self, client: TestClient
    ) -> None:
        analyst = {"X-Sentinel-Role": "analyst"}
        resp = client.post(
            "/v1/incidents/inc_1/investigations", json={}, headers=analyst
        )
        assert resp.status_code == 201
        resp = client.post(
            "/v1/ingestion/jobs",
            json={"source_id": SOURCE, "file_ref": "x"},
            headers=analyst,
        )
        assert resp.status_code == 403

    def test_unknown_role_is_denied(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/incidents/inc_1/investigations",
            json={},
            headers={"X-Sentinel-Role": "superadmin"},
        )
        assert resp.status_code == 403
        assert _problem(resp)["code"] == "sentinel.forbidden"

    def test_denial_does_not_leak_existence(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """A viewer probing a ghost id gets the same 403 as for a real id —
        denial precedes lookup, and is itself audit-recorded."""
        viewer = {"X-Sentinel-Role": "viewer"}
        real = client.post(
            "/v1/incidents/inc_1/investigations", json={}, headers=viewer
        )
        ghost = client.post(
            "/v1/incidents/inc_ghost/investigations", json={}, headers=viewer
        )
        assert real.status_code == ghost.status_code == 403
        assert real.json()["code"] == ghost.json()["code"]
        with SqliteUnitOfWork(
            tmp_path / "ws" / "sentinel.db"
        ) as uow:
            denials = uow.audit.list(target_id="investigate")
            assert any(a.action == "api.scope.denied" for a in denials)


class TestRouteTable:
    def test_no_generic_execute(self, client: TestClient) -> None:
        for path in ("/execute", "/v1/execute", "/v1/services/execute"):
            resp = client.post(path, json={})
            assert resp.status_code in (404, 405)
            if resp.status_code == 404:
                assert "problem" in resp.headers["content-type"]

    def test_every_documented_route_exists(self, client: TestClient) -> None:
        ok_gets = [
            "/v1/health",
            "/v1/capabilities",
            "/v1/services",
            f"/v1/services/{SERVICE}",
            f"/v1/services/{SERVICE}/operations",
            f"/v1/services/{SERVICE}/metrics",
            f"/v1/services/{SERVICE}/slos",
            "/v1/anomalies",
            "/v1/incidents",
            "/v1/incidents/inc_1",
            "/v1/incidents/inc_1/timeline",
            "/v1/evidence/ev_metric_w1",
            "/v1/integrations/status",
        ]
        for path in ok_gets:
            resp = client.get(path)
            assert resp.status_code == 200, f"{path}: {resp.status_code}"
