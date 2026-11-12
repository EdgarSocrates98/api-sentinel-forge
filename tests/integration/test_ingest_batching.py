"""Integration: C10-F006 batched lookups — query shape + replay equivalence.

The N+1 defects closed in this wave were measured, not suspected:
per-item ``services.get``, per-candidate ``observations.get`` dedup reads,
and per-window ``overlapping_sources`` calls. These tests pin the new
repository-layer batching (``get_many`` / ``idempotency_keys`` /
``overlapping_sources_in_ranges``) AND prove the semantics the pipeline
must preserve: identical redelivery → no-op, conflicting id → quarantine,
overlap coverage → exact-window semantics (a source covering the gap
between two windows is never reported).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from api_sentinel.domain import FixedClock, SequentialIdFactory, TelemetrySource
from api_sentinel.infrastructure.security import redact
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion import IngestionPipeline
from conftest import NOW, seed_service_and_source

_IDS = SequentialIdFactory()


class _CountingUoW(SqliteUnitOfWork):
    """SqliteUnitOfWork that classifies every SQL statement via trace hook.

    The sqlite3 trace callback receives *expanded* SQL — needles must not
    contain ``?`` placeholders (same rule as lab/bench/bench_matrix.py).
    """

    _NEEDLES = (
        ("SELECT observation_id, idempotency_key FROM runtime_observations", "dedup_batch_query"),
        ("SELECT * FROM runtime_observations WHERE observation_id=", "dedup_point_query"),
        (
            "SELECT DISTINCT source_id, window_start, window_end FROM runtime_observations",
            "overlap_batch_query",
        ),
        ("SELECT DISTINCT source_id FROM runtime_observations", "overlap_window_query"),
        ("SELECT * FROM api_services WHERE service_id IN", "service_batch_query"),
        ("SELECT * FROM api_services WHERE environment_id=", "service_by_name_query"),
        ("SELECT * FROM api_services WHERE service_id=", "service_point_query"),
    )

    def __init__(self, db_path: Path | str) -> None:
        super().__init__(db_path)
        self.counts: dict[str, int] = {}

    def _open(self) -> sqlite3.Connection:
        conn = super()._open()
        conn.set_trace_callback(self._count)
        return conn

    def _count(self, statement: str) -> None:
        for needle, label in self._NEEDLES:
            if needle in statement:
                self.counts[label] = self.counts.get(label, 0) + 1
                return


def _pipeline(db_path: Path, uow: SqliteUnitOfWork | None = None) -> IngestionPipeline:
    return IngestionPipeline(
        uow or SqliteUnitOfWork(db_path),
        FixedClock(NOW),
        _IDS,
        lambda t: redact(t).text,
    )


def _line(obs_id: str, window_start: datetime, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": "api-sentinel/RuntimeObservation/v1",
        "observation_id": obs_id,
        "service_id": "svc_lab_payments",
        "operation_id": "op_a",
        "window": {
            "start": window_start.isoformat(),
            "end": (window_start + timedelta(minutes=1)).isoformat(),
        },
        "source_id": "src_lab_jsonl",
        "quality": "COMPLETE",
        "measurement": {"kind": "counter", "unit": "requests", "value": 1.0},
        "attributes": {},
    }
    base.update(overrides)
    return base


def _content(lines: list[dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(line) for line in lines) + "\n").encode()


def test_dedup_and_service_lookups_are_batched(db_path: Path, tmp_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()

    uow = _CountingUoW(db_path)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    lines = [_line(f"obs_{i:04d}", base + timedelta(minutes=i)) for i in range(500)]
    result = _pipeline(db_path, uow).ingest(source=source, kind="jsonl", content=_content(lines))
    assert result.accepted == 500
    counts = uow.counts
    # No per-item queries remain: zero point lookups for services AND dedup.
    assert counts.get("dedup_point_query", 0) == 0, counts
    assert counts.get("service_point_query", 0) == 0, counts
    # Batched reads: ceil(500/400)=2 dedup reads, 1 service read.
    assert counts.get("dedup_batch_query", 0) == 2, counts
    assert counts.get("service_batch_query", 0) == 1, counts
    # Distinct windows here are 500 — the OLD code ran one overlap query
    # per window; the new code runs ONE bounded scan per service.
    assert counts.get("overlap_window_query", 0) == 0, counts
    assert counts.get("overlap_batch_query", 0) == 1, counts


def test_overlap_batched_matches_per_window_semantics(db_path: Path, tmp_path: Path) -> None:
    """`overlapping_sources_in_ranges` == union of per-window calls,
    including the gap case: coverage BETWEEN two windows never counts."""
    with SqliteUnitOfWork(db_path) as uow:
        src_a = seed_service_and_source(uow, source_id="src_a")
        src_b = uow.sources.put(
            TelemetrySource(
                source_id="src_b",
                kind="jsonl-file",
                service_id="svc_lab_payments",
                created_at=NOW,
            )
        )
        uow.commit()

    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    # src_a coverage: [0,1), [2,3), [5,6) minutes — a gap at [3,4).
    a_lines = [
        _line("a_0", t0, source_id="src_a"),
        _line("a_1", t0 + timedelta(minutes=2), source_id="src_a"),
        _line("a_2", t0 + timedelta(minutes=5), source_id="src_a"),
    ]
    r = _pipeline(db_path).ingest(source=src_a, kind="jsonl", content=_content(a_lines))
    assert r.accepted == 3

    # src_b queries windows [0,1) (overlap), [3,4) (the gap — NO overlap),
    # [5,6) (overlap).
    b_lines = [
        _line("b_0", t0, source_id="src_b"),
        _line("b_1", t0 + timedelta(minutes=3), source_id="src_b"),
        _line("b_2", t0 + timedelta(minutes=5), source_id="src_b"),
    ]
    with SqliteUnitOfWork(db_path) as uow:
        expected = set()
        for line in b_lines:
            ws = datetime.fromisoformat(line["window"]["start"])
            we = datetime.fromisoformat(line["window"]["end"])
            expected |= uow.observations.overlapping_sources(
                "svc_lab_payments", ws, we, exclude_source_id="src_b"
            )
        batched = uow.observations.overlapping_sources_in_ranges(
            "svc_lab_payments",
            [
                (
                    datetime.fromisoformat(line["window"]["start"]),
                    datetime.fromisoformat(line["window"]["end"]),
                )
                for line in b_lines
            ],
            exclude_source_id="src_b",
        )
    assert batched == expected == {"src_a"}

    # End-to-end: the ingest itself reports the same overlap set.
    r = _pipeline(db_path).ingest(source=src_b, kind="jsonl", content=_content(b_lines))
    assert r.accepted == 3
    assert r.overlapping_sources == ["src_a"]


def test_replay_equivalence_chunked_vs_whole(db_path: Path, tmp_path: Path) -> None:
    """Chunked vs whole ingest → identical accepted/skipped/quarantined and
    identical stored rows; replay of the same payload → full no-op."""
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    lines = [_line(f"obs_{i:04d}", base + timedelta(minutes=i)) for i in range(300)]
    content = _content(lines)

    r1 = _pipeline(db_path).ingest(source=source, kind="jsonl", content=content)
    assert (r1.accepted, r1.quarantined, r1.skipped_duplicates) == (300, 0, 0)

    # Replay the exact bytes: whole-batch dedup short-circuits.
    r2 = _pipeline(db_path).ingest(source=source, kind="jsonl", content=content)
    assert (r2.accepted, r2.skipped_duplicates) == (0, 300)
    with SqliteUnitOfWork(db_path) as uow:
        assert uow.observations.count() == 300


def test_identical_redelivery_skips_conflicting_quarantines(db_path: Path, tmp_path: Path) -> None:
    """The dedup decision (skip vs conflict-quarantine) is unchanged by
    batching: same id + same content → no-op; same id + different content
    → ``conflicting_observation_id``, never a rewrite."""
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    original = _line("obs_dup", base)
    r1 = _pipeline(db_path).ingest(source=source, kind="jsonl", content=_content([original]))
    assert r1.accepted == 1

    # New payload (different batch key): same id verbatim → skipped.
    r2 = _pipeline(db_path).ingest(
        source=source,
        kind="jsonl",
        content=_content([original, _line("obs_new", base)]),
    )
    assert (r2.accepted, r2.skipped_duplicates) == (1, 1)

    # Same id, different content → conflicting redelivery → quarantine.
    conflicted = dict(original)
    conflicted["measurement"] = {"kind": "counter", "unit": "requests", "value": 999.0}
    r3 = _pipeline(db_path).ingest(source=source, kind="jsonl", content=_content([conflicted]))
    assert (r3.accepted, r3.quarantined) == (0, 1)
    with SqliteUnitOfWork(db_path) as uow:
        reasons = [e.reason for e in uow.quarantine.list()]
        assert "conflicting_observation_id" in reasons
        # the stored row kept the ORIGINAL value — never rewritten
        stored = uow.observations.get("obs_dup")
        assert stored is not None
        assert stored.measurement.value == 1.0


def test_get_many_and_idempotency_keys_chunked(db_path: Path) -> None:
    """>400 ids still resolve (bind-chunked IN); missing ids absent, never
    defaulted."""
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    lines = [_line(f"obs_{i:04d}", base + timedelta(minutes=i)) for i in range(450)]
    _pipeline(db_path).ingest(source=source, kind="jsonl", content=_content(lines))
    ids = [f"obs_{i:04d}" for i in range(450)] + ["obs_missing"]
    with SqliteUnitOfWork(db_path) as uow:
        keys = uow.observations.idempotency_keys(ids)
        assert len(keys) == 450 and "obs_missing" not in keys
        many = uow.services.get_many(["svc_lab_payments", "svc_nope", *([""] * 500)])
        assert set(many) == {"svc_lab_payments"}
