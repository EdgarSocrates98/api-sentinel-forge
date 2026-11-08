"""Integration: ingest → detect → incident lifecycle, persisted honestly.

LAB-001 (elevated latency) and LAB-002 (persistent 503) prove the cycle:
exactly one incident per grouped failure signature, replay idempotent,
journal append-only, named errors for unknown services and rule kinds.

LAB-002 encoding note: the fixture carries request counts as delta
`counter` observations in `api-sentinel/RuntimeObservation/v1`, with the
HTTP status as the `http.response.status_code` attribute — the canonical
request plane the metrics context already classifies (5xx → error).
"""

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import seed_service_and_source
from typer.testing import CliRunner

from api_sentinel.application.use_cases import (
    ListIncidents,
    RunDetection,
    ShowIncident,
    TransitionIncident,
)
from api_sentinel.cli import app, main
from api_sentinel.detection import DetectionRule, EvaluationOutcome
from api_sentinel.domain import (
    ConflictError,
    FixedClock,
    NotFoundError,
    ObservationBatch,
    Provenance,
    SequentialIdFactory,
    ServiceRelease,
    ValidationError,
)
from api_sentinel.domain.quality import MeasurementKind, QualityState
from api_sentinel.incidents import EventKind, IncidentStatus
from api_sentinel.infrastructure.security import redact
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion import IngestionPipeline
from api_sentinel.ingestion.models import (
    NormalizedMeasurement,
    NormalizedObservation,
)

LAB1 = (
    Path(__file__).resolve().parents[2]
    / "lab" / "fixtures" / "lab-001-elevated-latency.jsonl"
)
LAB2 = (
    Path(__file__).resolve().parents[2]
    / "lab" / "fixtures" / "lab-002-persistent-503.jsonl"
)
NOW = datetime(2026, 1, 1, 0, 11, tzinfo=UTC)
W0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
W6 = datetime(2026, 1, 1, 0, 6, tzinfo=UTC)
W10 = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)

runner = CliRunner()
_IDS = SequentialIdFactory()


def _redactor(text: str) -> str:
    return redact(text).text


def _detect(db_path: Path, **kw):
    params = {"service_id": "svc_lab_checkout", "now": NOW}
    params.update(kw)
    return RunDetection(
        SqliteUnitOfWork(db_path), FixedClock(NOW), _IDS
    ).execute(**params)


def _ingest_lab2(db_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(
            uow, service_id="svc_lab_checkout", service_name="checkout-api"
        )
        uow.commit()
    result = IngestionPipeline(
        SqliteUnitOfWork(db_path), FixedClock(NOW), _IDS, _redactor
    ).ingest(source=source, kind="jsonl", content=LAB2.read_bytes())
    assert result.accepted == 16


def _ingest_lab1(db_path: Path, *, p95_ms: float = 300.0) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.detection_rules.put(
            DetectionRule(
                rule_id="rule_p95",
                revision=1,
                service_id="svc_lab_payments",
                kind="p95_over_latency_target",
                config={
                    "max_p95_ms": p95_ms,
                    "min_requests": 10,
                    "severity": "SEV3",
                },
                created_at=NOW,
            )
        )
        uow.commit()
    result = IngestionPipeline(
        SqliteUnitOfWork(db_path), FixedClock(NOW), _IDS, _redactor
    ).ingest(source=source, kind="jsonl", content=LAB1.read_bytes())
    assert result.accepted == 8


# --- LAB-002: persistent 503 → one incident, replay-idempotent -----------------


def test_lab002_persistent_503_fires_error_ratio_once(db_path: Path) -> None:
    _ingest_lab2(db_path)
    report = _detect(db_path)
    assert report.rules_seeded is True
    (anomaly,) = report.anomalies
    assert anomaly.rule.detector_id == "error_ratio_over_limit"
    assert anomaly.rule.rule_id == (
        "rule_error_ratio_over_limit_svc_lab_checkout"
    )
    # 573 errors / 999 eligible over the 10-minute window.
    assert "(573" in anomaly.explanation.math
    assert anomaly.explanation.math.endswith("> 0.05")
    (incident,) = report.incidents_created
    assert incident.status is IncidentStatus.DETECTED
    assert incident.severity.value == "SEV3"
    with SqliteUnitOfWork(db_path) as uow:
        stored = uow.anomalies.get(anomaly.anomaly_id)
        assert stored is not None and stored.incident_id == incident.incident_id
        events = uow.incident_events.list(incident.incident_id)
        assert [e.kind for e in events] == [EventKind.OPENED]
        assert uow.incident_events.count(incident.incident_id) == 1


def test_lab002_replay_never_duplicates(db_path: Path) -> None:
    _ingest_lab2(db_path)
    first = _detect(db_path)
    second = _detect(db_path)
    assert second.anomalies == []
    assert second.deduplicated == [a.anomaly_id for a in first.anomalies]
    assert second.incidents_created == []
    with SqliteUnitOfWork(db_path) as uow:
        assert len(uow.incidents.list()) == 1
        assert len(uow.anomalies.list_for_service("svc_lab_checkout")) == 1
        incident = uow.incidents.list()[0]
        # The journal did not grow on replay — deterministic event ids.
        assert uow.incident_events.count(incident.incident_id) == 1


def test_lab002_clean_window_journals_recovery_observation(db_path: Path) -> None:
    _ingest_lab2(db_path)
    first = _detect(db_path)
    incident_id = first.incidents_created[0].incident_id
    # Minutes 6-9 carry only 2xx: the grouping rule now evaluates clean.
    clean = _detect(db_path, window_start=W6, window_end=W10)
    assert clean.anomalies == []
    assert clean.recovery_observed == [incident_id]
    with SqliteUnitOfWork(db_path) as uow:
        events = uow.incident_events.list(incident_id)
        kinds = [e.kind for e in events]
        assert kinds == [EventKind.OPENED, EventKind.RECOVERY_OBSERVED]
        # The incident is still open — recovery is evidence, not a resolve.
        incident = uow.incidents.get(incident_id)
        assert incident is not None
        assert incident.status is IncidentStatus.DETECTED


# --- LAB-001: elevated latency → p95 anomaly -----------------------------------


def test_lab001_elevated_latency_fires_p95(db_path: Path) -> None:
    _ingest_lab1(db_path)
    report = RunDetection(
        SqliteUnitOfWork(db_path), FixedClock(NOW), _IDS
    ).execute(service_id="svc_lab_payments", now=NOW)
    (anomaly,) = report.anomalies
    assert anomaly.rule.detector_id == "p95_over_latency_target"
    assert "never averaged" in anomaly.explanation.math
    (incident,) = report.incidents_created
    assert incident.service_id == "svc_lab_payments"


# --- incident lifecycle via use cases ------------------------------------------


def test_incident_ack_resolve_reopen_flow(db_path: Path) -> None:
    _ingest_lab2(db_path)
    incident = _detect(db_path).incidents_created[0]

    listed = ListIncidents(SqliteUnitOfWork(db_path)).execute(open_only=True)
    assert [i.incident_id for i in listed] == [incident.incident_id]

    view = ShowIncident(SqliteUnitOfWork(db_path)).execute(
        incident_id=incident.incident_id
    )
    assert [e.kind for e in view.events] == [EventKind.OPENED]
    assert len(view.anomalies) == 1

    acked = TransitionIncident(
        SqliteUnitOfWork(db_path), FixedClock(NOW), _IDS
    ).execute(incident_id=incident.incident_id, to_status=IncidentStatus.TRIAGED)
    assert acked.acknowledged_at == NOW

    resolved = TransitionIncident(
        SqliteUnitOfWork(db_path), FixedClock(NOW), _IDS
    ).execute(incident_id=incident.incident_id, to_status=IncidentStatus.RESOLVED)
    assert resolved.resolved_at == NOW
    assert ListIncidents(SqliteUnitOfWork(db_path)).execute(open_only=True) == []

    reopened = TransitionIncident(
        SqliteUnitOfWork(db_path), FixedClock(NOW), _IDS
    ).execute(incident_id=incident.incident_id, to_status=IncidentStatus.REOPENED)
    assert reopened.resolved_at is None
    view = ShowIncident(SqliteUnitOfWork(db_path)).execute(
        incident_id=incident.incident_id
    )
    kinds = [e.kind for e in view.events]
    assert kinds == [EventKind.OPENED] + [EventKind.STATUS_TRANSITION] * 3


def test_invalid_transition_and_unknown_incident_are_named(db_path: Path) -> None:
    _ingest_lab2(db_path)
    incident = _detect(db_path).incidents_created[0]
    with pytest.raises(ValidationError, match="invalid incident transition"):
        TransitionIncident(
            SqliteUnitOfWork(db_path), FixedClock(NOW), _IDS
        ).execute(incident_id=incident.incident_id, to_status=IncidentStatus.CLOSED)
    with pytest.raises(NotFoundError, match="incident not found"):
        ShowIncident(SqliteUnitOfWork(db_path)).execute(incident_id="inc_ghost")
    with pytest.raises(NotFoundError, match="incident not found"):
        TransitionIncident(
            SqliteUnitOfWork(db_path), FixedClock(NOW), _IDS
        ).execute(incident_id="inc_ghost", to_status=IncidentStatus.TRIAGED)


def test_timeout_growth_after_deploy_fires_with_release(
    db_path: Path,
) -> None:
    """Release inside the lookback is recorded as evidence, not cause."""
    with SqliteUnitOfWork(db_path) as uow:
        seed_service_and_source(
            uow, service_id="svc_lab_checkout", service_name="checkout-api"
        )
        uow.releases.put(
            ServiceRelease(
                release_id="rel_lab_1",
                service_id="svc_lab_checkout",
                version="v2.4.0",
                deployed_at=W0,
                created_at=W0,
            )
        )
        obs = [
            NormalizedObservation(
                observation_id=f"to_{i}",
                service_id="svc_lab_checkout",
                operation_id=None,
                release_id=None,
                window_start=W0,
                window_end=W10,
                ingest_time=NOW,
                processing_time=NOW,
                source_id="src_lab_jsonl",
                batch_id="b1",
                quality=QualityState.COMPLETE,
                measurement=NormalizedMeasurement(
                    kind=MeasurementKind.COUNTER,
                    unit="requests",
                    value=10.0,
                    temporality="delta",
                ),
                attributes={"error.kind": "timeout"},
                idempotency_key=f"to-{i}",
            )
            for i in range(7)
        ] + [
            NormalizedObservation(
                observation_id="ok_1",
                service_id="svc_lab_checkout",
                operation_id=None,
                release_id=None,
                window_start=W0,
                window_end=W10,
                ingest_time=NOW,
                processing_time=NOW,
                source_id="src_lab_jsonl",
                batch_id="b1",
                quality=QualityState.COMPLETE,
                measurement=NormalizedMeasurement(
                    kind=MeasurementKind.COUNTER,
                    unit="requests",
                    value=30.0,
                    temporality="delta",
                ),
                attributes={"http.response.status_code": 200},
                idempotency_key="ok-1",
            )
        ]
        uow.batches.put(
            ObservationBatch(
                batch_id="b1",
                provenance=Provenance(
                    source_id="src_lab_jsonl",
                    observed_at=W0,
                    received_at=NOW,
                ),
                item_count=len(obs),
            )
        )
        for o in obs:
            uow.observations.put(o)
        uow.commit()
    report = _detect(db_path)
    detectors = {a.rule.detector_id for a in report.anomalies}
    assert "timeout_growth_after_deploy" in detectors
    anomaly = next(
        a for a in report.anomalies
        if a.rule.detector_id == "timeout_growth_after_deploy"
    )
    assert any(e.startswith("rel:rel_lab_1@") for e in anomaly.evidence_refs)


# --- negative paths -------------------------------------------------------------


def test_detect_unknown_service_is_a_named_error(db_path: Path) -> None:
    with pytest.raises(NotFoundError, match="service not found"):
        _detect(db_path, service_id="svc_ghost")


def test_detect_unknown_rule_kind_is_a_named_error(db_path: Path) -> None:
    _ingest_lab2(db_path)
    with SqliteUnitOfWork(db_path) as uow:
        uow.detection_rules.put(
            DetectionRule(
                rule_id="rule_bad",
                revision=1,
                service_id="svc_lab_checkout",
                kind="not_a_real_kind",
                config={},
                created_at=NOW,
            )
        )
        uow.commit()
    with pytest.raises(ValidationError, match="unknown detection rule kind"):
        _detect(db_path)


def test_detect_without_observations_is_a_named_error(db_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        seed_service_and_source(
            uow, service_id="svc_lab_checkout", service_name="checkout-api"
        )
        uow.commit()
    with pytest.raises(ValidationError, match="no stored observations"):
        _detect(db_path)


# --- persistence invariants ------------------------------------------------------


def test_incident_events_repo_has_no_update_or_delete_path(db_path: Path) -> None:
    _ingest_lab2(db_path)
    incident = _detect(db_path).incidents_created[0]
    with SqliteUnitOfWork(db_path) as uow:
        public = [n for n in dir(uow.incident_events) if not n.startswith("_")]
        assert sorted(public) == ["append", "count", "list"]
        event = uow.incident_events.list(incident.incident_id)[0]
        before = uow.incident_events.count(incident.incident_id)
        uow.incident_events.append(event)  # deterministic id → no-op
        assert uow.incident_events.count(incident.incident_id) == before


def test_incident_revision_conflict_is_a_named_error(db_path: Path) -> None:
    _ingest_lab2(db_path)
    incident = _detect(db_path).incidents_created[0]
    with SqliteUnitOfWork(db_path) as uow:
        # A write against the current revision succeeds...
        current = incident.model_copy(
            update={"title": "renamed", "revision": incident.revision + 1}
        )
        uow.incidents.put(current, expected_revision=incident.revision)
        # ...a stale read of the old revision is a named conflict.
        with pytest.raises(ConflictError, match="revision conflict"):
            uow.incidents.put(
                incident.model_copy(
                    update={"title": "stale", "revision": incident.revision + 1}
                ),
                expected_revision=incident.revision,
            )
        uow.rollback()


def test_default_rules_seed_per_service_without_collision(
    db_path: Path,
) -> None:
    """Default rule ids are per-service: seeding a second service must not
    collide on the (rule_id, revision) primary key."""
    _ingest_lab2(db_path)
    _detect(db_path)  # seeds for svc_lab_checkout
    with SqliteUnitOfWork(db_path) as uow:
        seed_service_and_source(
            uow, service_id="svc_second", service_name="second-api"
        )
        uow.commit()
    report = RunDetection(
        SqliteUnitOfWork(db_path), FixedClock(NOW), _IDS
    ).execute(service_id="svc_second", window_start=W0, window_end=W10)
    assert report.rules_seeded is True
    with SqliteUnitOfWork(db_path) as uow:
        first = uow.detection_rules.list("svc_lab_checkout")
        second = uow.detection_rules.list("svc_second")
        assert len(first) == len(second) == 6
        assert all(
            r.rule_id.endswith("_svc_lab_checkout") for r in first
        )
        assert all(r.rule_id.endswith("_svc_second") for r in second)


def test_rule_revisions_are_immutable(db_path: Path) -> None:
    _ingest_lab2(db_path)
    _detect(db_path)
    with SqliteUnitOfWork(db_path) as uow:
        rules = uow.detection_rules.list("svc_lab_checkout")
        assert {r.rule_id for r in rules} == {
            "rule_budget_burn_acceleration_svc_lab_checkout",
            "rule_error_ratio_over_limit_svc_lab_checkout",
            "rule_p95_over_latency_target_svc_lab_checkout",
            "rule_source_silence_svc_lab_checkout",
            "rule_status_unexpected_on_operation_svc_lab_checkout",
            "rule_timeout_growth_after_deploy_svc_lab_checkout",
        }
        assert all(r.revision == 1 for r in rules)
        # A rule revision is immutable: re-inserting the same (id, revision)
        # key is refused by the primary key.
        with pytest.raises(sqlite3.IntegrityError):
            uow.detection_rules.put(rules[0])
        uow.rollback()


def test_evaluation_report_is_honest_about_unknowns(db_path: Path) -> None:
    _ingest_lab2(db_path)
    report = _detect(db_path)
    by_kind = {e.kind: e for e in report.evaluations}
    # Every rule evaluated is represented with its honest outcome.
    assert by_kind["error_ratio_over_limit"].outcome is EvaluationOutcome.FIRED
    assert "no_slo_assessments_in_window" in (
        by_kind["budget_burn_acceleration"].unknowns
    )
    assert "no_unexpected_statuses_configured" in (
        by_kind["status_unexpected_on_operation"].unknowns
    )


# --- CLI smoke: ingest → detect → incident lifecycle ----------------------------


def test_cli_detect_incident_workflow(tmp_path: Path) -> None:
    ws = str(tmp_path / "ws")

    def invoke(*args: str) -> dict | list:
        result = runner.invoke(app, [*args, "--workspace", ws, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    svc = invoke("services", "--register", "checkout-api")
    src = invoke(
        "sources", "--register", "jsonl-file", "--service-id", svc["service_id"]
    )
    payload = LAB2.read_text(encoding="utf-8").replace(
        "svc_lab_checkout", svc["service_id"]
    ).replace("src_lab_jsonl", src["source_id"])
    fixture = tmp_path / "lab2-remapped.jsonl"
    fixture.write_text(payload, encoding="utf-8")
    invoke("ingest", "--source-id", src["source_id"], "--file", str(fixture))

    report = invoke(
        "detect",
        "--service-id", svc["service_id"],
        "--now", "2026-01-01T00:11:00+00:00",
    )
    assert report["rules_seeded"] is True
    assert len(report["anomalies"]) == 1
    assert len(report["incidents_created"]) == 1
    incident_id = report["incidents_created"][0]["incident_id"]

    incidents = invoke("incidents", "--status", "open")
    assert [i["incident_id"] for i in incidents] == [incident_id]

    view = invoke("incident", "show", incident_id)
    assert view["incident"]["status"] == "DETECTED"
    assert [e["kind"] for e in view["events"]] == ["opened"]
    assert len(view["anomalies"]) == 1

    acked = invoke("incident", "ack", incident_id, "--note", "triaging")
    assert acked["status"] == "TRIAGED"
    resolved = invoke("incident", "resolve", incident_id)
    assert resolved["status"] == "RESOLVED"
    reopened = invoke("incident", "reopen", incident_id)
    assert reopened["status"] == "REOPENED"
    assert reopened["resolved_at"] is None

    view = invoke("incident", "show", incident_id)
    assert [e["kind"] for e in view["events"]] == [
        "opened",
        "status_transition",
        "status_transition",
        "status_transition",
    ]


def test_cli_detect_unknown_service_named_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    ws = str(tmp_path / "ws")
    runner.invoke(app, ["services", "--register", "svc", "--workspace", ws])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "api-sentinel",
            "detect",
            "--service-id", "svc_ghost",
            "--workspace", ws,
            "--json",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "sentinel.not_found" in capsys.readouterr().err


def test_cli_incidents_empty_workspace_lists_nothing(tmp_path: Path) -> None:
    ws = str(tmp_path / "ws")
    result = runner.invoke(
        app, ["incidents", "--status", "open", "--workspace", ws, "--json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
