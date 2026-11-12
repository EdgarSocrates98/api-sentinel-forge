"""Integration: ingest -> slo define -> evaluate -> health, persisted honestly.

LAB-015: changing an SLO creates a new revision; the prior one is preserved
and its evaluation reproducible. Re-evaluation of the same window+revision is
idempotent (replace, not duplicate).
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from api_sentinel.application.use_cases import (
    DefineSlo,
    EvaluateSlo,
    GetServiceHealth,
)
from api_sentinel.cli import app, main
from api_sentinel.domain import (
    ConflictError,
    FixedClock,
    NotFoundError,
    SequentialIdFactory,
    ValidationError,
)
from api_sentinel.infrastructure.security import redact
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion import IngestionPipeline
from api_sentinel.reliability import AssessmentState, HealthState
from conftest import seed_service_and_source

FIXTURE = (
    Path(__file__).resolve().parents[2] / "lab" / "fixtures" / "lab-001-elevated-latency.jsonl"
)
NOW = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
WINDOW_START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 1, 1, 0, 8, tzinfo=UTC)

runner = CliRunner()

_IDS = SequentialIdFactory()  # shared per test run: ids stay unique across calls


def _redactor(text: str) -> str:
    return redact(text).text


def _ids() -> SequentialIdFactory:
    return _IDS


def _ingest_lab(db_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    result = IngestionPipeline(
        SqliteUnitOfWork(db_path), FixedClock(NOW), _ids(), _redactor
    ).ingest(source=source, kind="jsonl", content=FIXTURE.read_bytes())
    assert result.accepted == 8


def _define(db_path: Path, **kw):
    params = {"service_id": "svc_lab_payments", "target": 0.99, "window_seconds": 3600}
    params.update(kw)
    return DefineSlo(SqliteUnitOfWork(db_path), FixedClock(NOW), _ids()).execute(**params)


def _evaluate(db_path: Path, **kw):
    return EvaluateSlo(SqliteUnitOfWork(db_path), FixedClock(NOW), _ids()).execute(
        slo_id=kw.pop("slo_id"), window_start=WINDOW_START, window_end=WINDOW_END, **kw
    )


def test_lab_fixture_evaluates_end_to_end(db_path: Path) -> None:
    _ingest_lab(db_path)
    definition = _define(db_path)
    asmt = _evaluate(db_path, slo_id=definition.slo_id)
    # 8 duration histograms, all COMPLETE: eligible = sum(count) = 951, all good.
    assert asmt.state is AssessmentState.MET
    assert asmt.sli == pytest.approx(1.0)
    assert asmt.eligible == pytest.approx(951.0)
    assert asmt.bad == 0.0 and asmt.budget_consumed == pytest.approx(0.0)
    assert asmt.coverage.observations_included == 8
    # 7 of 8 windows ended >300s before the injected ingest clock -> STALE.
    # Quality stays a separate dimension (ADR-017/018): state is still MET.
    assert asmt.quality.value == "STALE"
    # percentiles came from merged buckets, including the repaired per-bucket rows
    assert any("bucket_semantics" in lim for lim in asmt.limitations)


def test_reevaluation_same_slot_is_idempotent(db_path: Path) -> None:
    _ingest_lab(db_path)
    definition = _define(db_path)
    first = _evaluate(db_path, slo_id=definition.slo_id)
    second = _evaluate(db_path, slo_id=definition.slo_id)
    assert first == second  # same inputs + same revision -> identical result
    with SqliteUnitOfWork(db_path) as uow:
        rows = uow.slo_assessments.list(definition.slo_id)
        assert len(rows) == 1  # replaced, never duplicated


def test_slo_revision_preserved_and_reproducible(db_path: Path) -> None:
    _ingest_lab(db_path)
    rev1 = _define(db_path, slo_id="slo_avail", target=0.99)
    rev2 = _define(db_path, slo_id="slo_avail", target=0.5)
    assert (rev1.revision, rev2.revision) == (1, 2)
    with SqliteUnitOfWork(db_path) as uow:
        defs = uow.slo_definitions.list()
        assert len(defs) == 2  # LAB-015: prior revision preserved
        assert {d.revision for d in defs} == {1, 2}
    # Reproducible: rev1 still evaluates under its own definition.
    asmt = _evaluate(db_path, slo_id="slo_avail", revision=1)
    assert asmt.revision == 1 and asmt.target == 0.99
    assert asmt.state is AssessmentState.MET
    again = _evaluate(db_path, slo_id="slo_avail", revision=1)
    assert again == asmt


def test_conflicting_revision_is_a_named_error(db_path: Path) -> None:
    _ingest_lab(db_path)  # seeds svc_lab_payments
    _define(db_path, slo_id="slo_x", revision=1, target=0.99)
    with pytest.raises(ConflictError):
        _define(db_path, slo_id="slo_x", revision=1, target=0.5)
    # identical re-define is an idempotent no-op
    same = _define(db_path, slo_id="slo_x", revision=1, target=0.99)
    assert same.revision == 1


def test_unknown_slo_evaluate_is_a_named_error(db_path: Path) -> None:
    with pytest.raises(NotFoundError, match="slo not found"):
        _evaluate(db_path, slo_id="slo_ghost")


def test_define_requires_existing_service(db_path: Path) -> None:
    with pytest.raises(NotFoundError, match="service not found"):
        DefineSlo(SqliteUnitOfWork(db_path), FixedClock(NOW), _ids()).execute(
            service_id="ghost", target=0.99, window_seconds=60
        )


def test_evaluate_rejects_inverted_window(db_path: Path) -> None:
    _ingest_lab(db_path)
    definition = _define(db_path)
    with pytest.raises(ValidationError):
        EvaluateSlo(SqliteUnitOfWork(db_path), FixedClock(NOW), _ids()).execute(
            slo_id=definition.slo_id,
            window_start=WINDOW_END,
            window_end=WINDOW_START,
        )


def test_health_after_lab_ingest(db_path: Path) -> None:
    _ingest_lab(db_path)
    report = GetServiceHealth(SqliteUnitOfWork(db_path), FixedClock(NOW)).execute(
        service_id="svc_lab_payments"
    )
    # Latest window 00:07-00:08 is fresh at NOW=00:10; no error evidence.
    assert report.state is HealthState.AVAILABLE
    assert report.latest_indicators is not None
    assert report.latest_indicators.eligible_requests == pytest.approx(118.0)


def test_slo_persists_and_rehydrates(db_path: Path) -> None:
    _ingest_lab(db_path)
    definition = _define(db_path)
    asmt = _evaluate(db_path, slo_id=definition.slo_id)
    with SqliteUnitOfWork(db_path) as uow:
        got = uow.slo_assessments.get(asmt.assessment_id)
        assert got is not None
        assert got == asmt
        assert got.per_source[0].source_id == "src_lab_jsonl"


def test_cli_slo_workflow(tmp_path: Path) -> None:
    ws = str(tmp_path / "ws")

    def invoke(*args: str) -> dict:
        result = runner.invoke(app, [*args, "--workspace", ws, "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    svc = invoke("services", "--register", "payments-api")
    src = invoke("sources", "--register", "jsonl-file", "--service-id", svc["service_id"])
    # The lab fixture names svc_lab_payments/src_lab_jsonl; the CLI registers
    # generated ids — remap so origin+service validation passes (no guessing).
    payload = (
        FIXTURE.read_text(encoding="utf-8")
        .replace("svc_lab_payments", svc["service_id"])
        .replace("src_lab_jsonl", src["source_id"])
    )
    fixture = tmp_path / "lab-remapped.jsonl"
    fixture.write_text(payload, encoding="utf-8")
    invoke("ingest", "--source-id", src["source_id"], "--file", str(fixture))
    definition = invoke(
        "slo",
        "define",
        "--service-id",
        svc["service_id"],
        "--target",
        "0.99",
        "--window",
        "1h",
        "--slo-id",
        "slo_cli",
    )
    assert definition["slo_id"] == "slo_cli" and definition["revision"] == 1
    asmt = invoke(
        "slo",
        "evaluate",
        "--slo-id",
        "slo_cli",
        "--window-start",
        "2026-01-01T00:00:00+00:00",
        "--window-end",
        "2026-01-01T00:08:00+00:00",
    )
    assert asmt["state"] == "MET"
    assert asmt["eligible"] == pytest.approx(951.0)
    assert asmt["assessment_id"].startswith("asmt_")
    defs = invoke("slo", "list")
    assert len(defs) == 1
    health = invoke("health", "--service-id", svc["service_id"])
    # Fixture windows are months behind the real clock -> stale source, honest
    # UNVERIFIED rather than fabricated health (LAB-007).
    assert health["state"] == "UNVERIFIED"
    assert health["condition"] == "source_stale"
    assert "unknowns" in health
    assert "absent_indicators" in health and "sources" in health


def test_cli_evaluate_unknown_slo_named_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    ws = str(tmp_path / "ws")
    runner.invoke(app, ["services", "--register", "svc", "--workspace", ws])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "api-sentinel",
            "slo",
            "evaluate",
            "--slo-id",
            "slo_ghost",
            "--window-start",
            "2026-01-01T00:00:00+00:00",
            "--window-end",
            "2026-01-01T01:00:00+00:00",
            "--workspace",
            ws,
            "--json",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "sentinel.not_found" in capsys.readouterr().err


def test_cli_health_unknown_service_named_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    ws = str(tmp_path / "ws")
    runner.invoke(app, ["services", "--register", "svc", "--workspace", ws])
    monkeypatch.setattr(
        sys,
        "argv",
        ["api-sentinel", "health", "--service-id", "svc_ghost", "--workspace", ws, "--json"],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "sentinel.not_found" in capsys.readouterr().err
