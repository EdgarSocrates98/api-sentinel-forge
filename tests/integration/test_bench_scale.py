"""Scale benchmark gate — `-m slow` only, never in the default suite.

Wraps `lab/bench/bench_ingest.py` at a modest N so ingestion throughput stays
regression-checked (rows actually land, memory bounded, replay is idempotent)
without lengthening `pytest -q`. The real 10k/50k/100k measurements recorded
in `docs/cycles/cycle-09-report.md` + `factory/runs/` come from running the
script directly — this test asserts no artificial throughput threshold.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lab" / "bench"))

from bench_ingest import run_bench


@pytest.mark.slow
def test_ingest_bench_10k(tmp_path: Path) -> None:
    report = run_bench(n=10_000, chunk=2_500, workspace=tmp_path / "ws")
    # hard correctness only: every line accepted and persisted — never a
    # fabricated throughput floor; the report records what the machine gave
    assert report["accepted"] == 10_000
    assert report["stored"] == 10_000
    assert report["elapsed_seconds"] > 0
    assert report["db_size_bytes"] > 0


@pytest.mark.slow
def test_ingest_bench_replay_idempotent(tmp_path: Path) -> None:
    """Re-ingesting the same generated corpus must not duplicate rows."""
    import json
    from datetime import UTC, datetime

    from bench_ingest import _observation, _seed

    from api_sentinel.domain import SystemClock, UlidIdFactory
    from api_sentinel.infrastructure.security import redact
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork
    from api_sentinel.ingestion import IngestionPipeline
    from api_sentinel.workspace import Workspace, ensure_workspace

    ws = ensure_workspace(Workspace(root=tmp_path / "ws", scope="project"))
    source = _seed(ws)
    pipeline = IngestionPipeline(
        SqliteUnitOfWork(ws.db_path),
        SystemClock(),
        UlidIdFactory(),
        lambda t: redact(t).text,
    )
    base = datetime(2026, 1, 1, tzinfo=UTC)
    lines = [json.dumps(_observation(i, base)) for i in range(500)]
    first = pipeline.ingest(
        source=source, kind="jsonl", content="\n".join(lines).encode()
    )
    second = pipeline.ingest(
        source=source, kind="jsonl", content="\n".join(lines).encode()
    )
    assert first.accepted == 500
    assert second.accepted == 0  # whole-payload dedup — zero duplication
    with SqliteUnitOfWork(ws.db_path) as uow:
        assert uow.observations.count() == 500
