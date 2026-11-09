"""Cycle-10 F006 benchmark matrix — identical datasets, honest before/after.

Generates deterministic datasets (no RNG; every byte is a function of the
line index), writes them under `--datasets`, then drives the real
`IngestionPipeline` (parse → normalize → dedup → overlap → persist) inside
the real `SqliteUnitOfWork`, measuring per shape:

- wall time per ingest chunk and total;
- peak traced Python memory (`tracemalloc`);
- resulting `sentinel.db` size;
- SQLite statement counts, classified by SQL text (`set_trace_callback`) —
  this is what proves/disproves the two measured N+1 defects:
  per-observation point dedup lookups and per-window overlap queries.

Datasets are content-addressed by sha256 in the report: before/after runs
that consume the same dataset directory ran identical inputs.

Shapes (each at --n observations unless noted):

- ``sequential``        contiguous windows per chunk (producer-realistic);
- ``dispersed``         every chunk spans ``--windows`` distinct 1-minute
                        windows (the measured worst case for the
                        per-window overlap check);
- ``out_of_order``      sequential windows, deterministic in-chunk shuffle;
- ``multi_source``      ``--sources`` channels covering the same windows —
                        exercises a *non-empty* overlap result;
- ``high_cardinality``  many distinct operations + high-card attributes;
- ``dup_heavy``         50% redelivered observation ids (intra- and
                        inter-chunk) — the dedup hot path;
- ``reprocess``         the same payload ingested twice; the metric is the
                        replay (batch-level idempotent no-op);
- ``large_file``        one single ingest call carrying all N lines
                        (requires ``--large-max-mib`` policy headroom);
- ``mid_batch_failure`` malformed lines interleaved — the quarantine path;
- ``concurrent``        ``--sources`` channels interleaved chunk-by-chunk in
                        one process (SQLite serializes writers — measured
                        honestly, not claimed as parallel).

Run:

    .venv/Scripts/python.exe lab/bench/bench_matrix.py --all
    .venv/Scripts/python.exe lab/bench/bench_matrix.py --shape dispersed --n 10000
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from api_sentinel.domain import (
    ApiService,
    ServiceEnvironment,
    SystemClock,
    TelemetrySource,
    UlidIdFactory,
)
from api_sentinel.infrastructure.security import redact
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion import IngestionPipeline
from api_sentinel.ingestion.models import IngestionPolicy
from api_sentinel.workspace import Workspace, ensure_workspace

BASE = datetime(2026, 2, 1, tzinfo=UTC)  # fixed anchor — datasets never drift
DEFAULT_OPS = 8
_SQL_CLASSES: tuple[tuple[str, str], ...] = (
    # Order matters: first substring match wins. NOTE: the sqlite3 trace
    # hook receives *expanded* SQL (bind values inlined) — needles must not
    # contain '?' placeholders.
    ("SELECT observation_id, idempotency_key FROM runtime_observations",
     "dedup_batch_query"),
    ("SELECT * FROM runtime_observations WHERE observation_id=",
     "dedup_point_query"),
    ("SELECT DISTINCT source_id, window_start, window_end FROM runtime_observations",
     "overlap_batch_query"),
    ("SELECT DISTINCT source_id FROM runtime_observations",
     "overlap_window_query"),
    ("INSERT OR IGNORE INTO runtime_observations", "observation_insert"),
    ("SELECT * FROM api_services WHERE service_id IN", "service_batch_query"),
    ("SELECT * FROM api_services WHERE environment_id=",
     "service_by_name_query"),
    ("SELECT * FROM api_services WHERE service_id=", "service_point_query"),
    ("INSERT INTO ingestion_quarantine", "quarantine_insert"),
    ("INSERT OR IGNORE INTO observation_batches", "batch_insert"),
)


class _CountingUoW(SqliteUnitOfWork):
    """SqliteUnitOfWork that classifies every executed SQL statement.

    Statement counting happens at the `sqlite3` trace hook — it measures
    the real queries the repositories issue, not call-site bookkeeping.
    """

    def __init__(self, db_path: Path | str, counts: dict[str, int]) -> None:
        super().__init__(db_path)
        self._counts = counts

    def _open(self) -> sqlite3.Connection:
        conn = super()._open()
        conn.set_trace_callback(self._count)
        return conn

    def _count(self, statement: str) -> None:
        for needle, label in _SQL_CLASSES:
            if needle in statement:
                self._counts[label] = self._counts.get(label, 0) + 1
                return
        self._counts["other"] = self._counts.get("other", 0) + 1


def _observation(
    i: int,
    end: datetime,
    *,
    service: str,
    source: str,
    n_ops: int = DEFAULT_OPS,
    extra_attrs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One `api-sentinel/RuntimeObservation/v1` line — deterministic in `i`."""
    attrs: dict[str, Any] = {
        "http.route": f"/bench/route{i % max(1, n_ops)}",
        "http.request.method": "GET",
        "http.response.status_code": 200 if i % 23 else 503,
        "bench.seq": i,
    }
    if extra_attrs:
        attrs.update(extra_attrs)
    return {
        "schema": "api-sentinel/RuntimeObservation/v1",
        "observation_id": f"obs_bench_{i:07d}",
        "service_id": service,
        "operation_id": f"op_bench_{i % max(1, n_ops)}",
        "release_id": None,
        "window": {
            "start": (end - timedelta(seconds=60)).isoformat(),
            "end": end.isoformat(),
        },
        "source_id": source,
        "quality": "COMPLETE",
        "measurement": {
            "kind": "histogram",
            "unit": "ms",
            "count": 20 + (i % 37),
            "sum": float((20 + (i % 37)) * 95),
            "buckets": [
                {"le": 50, "count": 5 + (i % 11)},
                {"le": 200, "count": 15 + (i % 29)},
                {"le": "+Inf", "count": 20 + (i % 37)},
            ],
        },
        "attributes": attrs,
    }


def _encode(lines: Iterable[dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(line) for line in lines) + "\n").encode()


def _write_dataset(path: Path, chunks: list[bytes]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with path.open("wb") as fh:
        for blob in chunks:
            digest.update(blob)
            fh.write(b"__CHUNK__\n")  # chunk boundary marker (never ingested)
            fh.write(blob)
    return f"sha256:{digest.hexdigest()}"


def _read_chunks(path: Path) -> list[bytes]:
    """Inverse of `_write_dataset` — split on the boundary marker."""
    raw = path.read_bytes()
    parts = raw.split(b"__CHUNK__\n")
    return [p for p in parts if p.strip()]


def _dataset_hash(path: Path) -> str:
    digest = hashlib.sha256()
    chunks = _read_chunks(path)
    for blob in chunks:
        digest.update(blob)
    return f"sha256:{digest.hexdigest()}"


def _seed(
    ws: Workspace, *, n_sources: int = 1, services: int = 1
) -> list[TelemetrySource]:
    ensure_workspace(ws)
    now = BASE
    out: list[TelemetrySource] = []
    with SqliteUnitOfWork(ws.db_path) as uow:
        env = uow.environments.put(
            ServiceEnvironment(
                environment_id="env_bench", name="bench", created_at=now
            )
        )
        for s in range(services):
            sid = f"svc_bench_{s}" if services > 1 else "svc_bench"
            uow.services.put(
                ApiService(
                    service_id=sid,
                    service_name=f"bench-api-{s}",
                    environment_id=env.environment_id,
                    created_at=now,
                    updated_at=now,
                )
            )
        for j in range(n_sources):
            sid = "svc_bench" if services == 1 else f"svc_bench_{j % services}"
            out.append(
                uow.sources.put(
                    TelemetrySource(
                        source_id=f"src_bench_{j}" if n_sources > 1 else "src_bench",
                        kind="jsonl-file",
                        service_id=sid,
                        created_at=now,
                    )
                )
            )
        uow.commit()
    return out


def _window_end_seq(i: int, offset: int, size: int) -> datetime:
    """Contiguous producer shape: ~size//100 distinct windows per chunk."""
    return BASE - timedelta(minutes=offset // 100 + i % max(1, size // 100))


def _window_end_dispersed(i: int, n_windows: int) -> datetime:
    """Worst case: every chunk spans `n_windows` distinct 1-min windows."""
    return BASE - timedelta(minutes=i % n_windows)


# --- dataset generators ----------------------------------------------------
# Each returns a list of chunk payloads (bytes). All deterministic.


def gen_sequential(n: int, chunk: int, args: argparse.Namespace) -> list[bytes]:
    chunks = []
    for offset in range(0, n, chunk):
        size = min(chunk, n - offset)
        chunks.append(
            _encode(
                _observation(
                    i, _window_end_seq(i, offset, size),
                    service="svc_bench", source="src_bench",
                )
                for i in range(offset, offset + size)
            )
        )
    return chunks


def gen_dispersed(n: int, chunk: int, args: argparse.Namespace) -> list[bytes]:
    chunks = []
    for offset in range(0, n, chunk):
        size = min(chunk, n - offset)
        chunks.append(
            _encode(
                _observation(
                    i, _window_end_dispersed(i, args.windows),
                    service="svc_bench", source="src_bench",
                )
                for i in range(offset, offset + size)
            )
        )
    return chunks


def gen_out_of_order(n: int, chunk: int, args: argparse.Namespace) -> list[bytes]:
    chunks = []
    for offset in range(0, n, chunk):
        size = min(chunk, n - offset)
        lines = [
            _observation(
                i, _window_end_seq(i, offset, size),
                service="svc_bench", source="src_bench",
            )
            for i in range(offset, offset + size)
        ]
        # Deterministic in-chunk shuffle: stride permutation, no RNG.
        stride = 7919
        order = sorted(range(size), key=lambda k: (k * stride) % size)
        chunks.append(_encode(lines[k] for k in order))
    return chunks


def gen_high_cardinality(n: int, chunk: int, args: argparse.Namespace) -> list[bytes]:
    n_ops = args.ops_high_card
    chunks = []
    for offset in range(0, n, chunk):
        size = min(chunk, n - offset)
        chunks.append(
            _encode(
                _observation(
                    i, _window_end_seq(i, offset, size),
                    service="svc_bench", source="src_bench", n_ops=n_ops,
                    extra_attrs={"enduser.id": f"user_{i % 5000}"},
                )
                for i in range(offset, offset + size)
            )
        )
    return chunks


def gen_dup_heavy(n: int, chunk: int, args: argparse.Namespace) -> list[bytes]:
    """50% identical redeliveries — the dedup `skipped` path under load.

    Odd positions replay an earlier line verbatim (same observation_id and
    same content → identical idempotency key → skipped, never rewritten).
    """
    chunks = []
    for offset in range(0, n, chunk):
        size = min(chunk, n - offset)
        lines: list[dict[str, Any]] = []
        for k in range(size):
            i = offset + k
            if k % 2 == 1:
                # verbatim redelivery of an earlier line in the same chunk
                lines.append(lines[k - 1])
                continue
            lines.append(
                _observation(
                    i, _window_end_seq(i, offset, size),
                    service="svc_bench", source="src_bench",
                )
            )
        chunks.append(_encode(lines))
    return chunks


def gen_mid_batch_failure(n: int, chunk: int, args: argparse.Namespace) -> list[bytes]:
    """10% malformed lines interleaved — quarantine under load."""
    chunks = []
    for offset in range(0, n, chunk):
        size = min(chunk, n - offset)
        raw: list[str] = []
        for k in range(size):
            i = offset + k
            if i % 10 == 5:
                raw.append('{"schema": "broken", "payload":')  # unparseable
            elif i % 10 == 8:
                raw.append(json.dumps({"schema": "unknown/Kind/v9", "x": 1}))
            else:
                raw.append(
                    json.dumps(
                        _observation(
                            i, _window_end_seq(i, offset, size),
                            service="svc_bench", source="src_bench",
                        )
                    )
                )
        chunks.append(("\n".join(raw) + "\n").encode())
    return chunks


def gen_multi_source(n: int, chunk: int, args: argparse.Namespace) -> list[bytes]:
    """One logical stream; `run` rewrites the declared source per channel."""
    n_windows = 120
    chunks = []
    for offset in range(0, n, chunk):
        size = min(chunk, n - offset)
        chunks.append(
            _encode(
                _observation(
                    i, BASE - timedelta(minutes=i % n_windows),
                    service="svc_bench", source="src_bench",
                )
                for i in range(offset, offset + size)
            )
        )
    return chunks


SHAPES: dict[str, tuple[str, Callable[[int, int, argparse.Namespace], list[bytes]]]] = {
    "sequential": ("contiguous windows per chunk", gen_sequential),
    "dispersed": ("chunks span --windows distinct 1-min windows", gen_dispersed),
    "out_of_order": ("sequential windows, deterministic shuffle", gen_out_of_order),
    "multi_source": ("N sources covering the same windows", gen_multi_source),
    "high_cardinality": ("--ops-high-card ops + user.id attr", gen_high_cardinality),
    "dup_heavy": ("50% redelivered observation ids", gen_dup_heavy),
    "mid_batch_failure": ("10% malformed lines → quarantine", gen_mid_batch_failure),
    "reprocess": ("same payload ingested twice — replay measured", gen_sequential),
    "large_file": ("single ingest call, all N lines", gen_sequential),
    "concurrent": ("--sources channels interleaved chunk-wise", gen_multi_source),
}


def _fresh_workspace(root: Path) -> Workspace:
    if root.exists():
        shutil.rmtree(root)
    return ensure_workspace(Workspace(root=root, scope="project"))


def _ingest_chunks(
    ws: Workspace,
    chunks: list[bytes],
    *,
    sources: list[TelemetrySource],
    policy: IngestionPolicy | None = None,
    source_for_chunk: Callable[[int], TelemetrySource] | None = None,
    rewrite_source: bool = False,
) -> dict[str, Any]:
    """Drive the real pipeline chunk-by-chunk; measure wall + SQL counts."""
    counts: dict[str, int] = {}
    pipeline = IngestionPipeline(
        _CountingUoW(ws.db_path, counts),
        SystemClock(),
        UlidIdFactory(),
        lambda t: redact(t).text,
        policy,
    )
    chunk_seconds: list[float] = []
    accepted = quarantined = skipped = 0
    overlap_seen: set[str] = set()
    tracemalloc.start()
    started = time.perf_counter()
    for c, blob in enumerate(chunks):
        source = source_for_chunk(c) if source_for_chunk else sources[0]
        if rewrite_source:
            # Rewrite the declared source_id to the ingesting channel so the
            # multi-source shape exercises real overlap detection.
            blob = blob.replace(b'"source_id": "src_bench"',
                                f'"source_id": "{source.source_id}"'.encode())
        t0 = time.perf_counter()
        result = pipeline.ingest(source=source, kind="jsonl", content=blob)
        chunk_seconds.append(round(time.perf_counter() - t0, 3))
        accepted += result.accepted
        quarantined += result.quarantined
        skipped += result.skipped_duplicates
        overlap_seen.update(result.overlapping_sources)
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "accepted": accepted,
        "quarantined": quarantined,
        "skipped_duplicates": skipped,
        "overlapping_sources": sorted(overlap_seen),
        "elapsed_seconds": round(elapsed, 3),
        "observations_per_second": round(accepted / elapsed, 1) if elapsed else 0,
        "chunk_seconds": chunk_seconds,
        "tracemalloc_peak_mib": round(peak / (1024 * 1024), 2),
        "sql_counts": dict(sorted(counts.items())),
    }


def _db_stats(ws: Workspace) -> dict[str, Any]:
    db_size = ws.db_path.stat().st_size
    wal = Path(str(ws.db_path) + "-wal")
    with SqliteUnitOfWork(ws.db_path) as uow:
        stored = uow.observations.count()
        quarantined = uow.quarantine.count()
    return {
        "stored_observations": stored,
        "stored_quarantine": quarantined,
        "db_size_bytes": db_size,
        "db_size_mib": round(db_size / (1024 * 1024), 2),
        "wal_size_bytes": wal.stat().st_size if wal.exists() else 0,
    }


def run_shape(
    shape: str,
    *,
    n: int,
    chunk: int,
    args: argparse.Namespace,
    work_root: Path,
    datasets: Path,
) -> dict[str, Any]:
    description = SHAPES[shape][0]
    gen = SHAPES[shape][1]
    dataset_path = datasets / f"{shape}.jsonl"
    eff_chunk = n if shape == "large_file" else chunk
    if not dataset_path.exists():
        digest = _write_dataset(dataset_path, gen(n, eff_chunk, args))
    else:
        digest = _dataset_hash(dataset_path)
    chunks = _read_chunks(dataset_path)

    ws = _fresh_workspace(work_root / f"ws_{shape}")
    policy = None
    if shape == "large_file":
        policy = IngestionPolicy(
            max_batch_bytes=args.large_max_mib * 1024 * 1024,
            max_items=max(n * 2, 100_000),
        )

    if shape == "multi_source":
        sources = _seed(ws, n_sources=args.sources)
        result = _ingest_chunks(
            ws, chunks, sources=sources,
            source_for_chunk=lambda c: sources[c % len(sources)],
            rewrite_source=True,
        )
    elif shape == "concurrent":
        sources = _seed(ws, n_sources=args.sources)
        # Interleave each source's chunk stream round-robin in one process —
        # SQLite serializes writers; this is the honest measured behavior.
        interleaved: list[tuple[int, bytes]] = []
        per_source = max(1, len(chunks) // len(sources))
        for c, blob in enumerate(chunks):
            interleaved.append((c // per_source % len(sources), blob))
        counts: dict[str, int] = {}
        pipeline = IngestionPipeline(
            _CountingUoW(ws.db_path, counts),
            SystemClock(),
            UlidIdFactory(),
            lambda t: redact(t).text,
        )
        chunk_seconds = []
        accepted = skipped = 0
        tracemalloc.start()
        started = time.perf_counter()
        for src_idx, blob in interleaved:
            source = sources[src_idx]
            blob = blob.replace(
                b'"source_id": "src_bench"',
                f'"source_id": "{source.source_id}"'.encode(),
            )
            t0 = time.perf_counter()
            r = pipeline.ingest(source=source, kind="jsonl", content=blob)
            chunk_seconds.append(round(time.perf_counter() - t0, 3))
            accepted += r.accepted
            skipped += r.skipped_duplicates
        elapsed = time.perf_counter() - started
        _c, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        result = {
            "accepted": accepted,
            "quarantined": 0,
            "skipped_duplicates": skipped,
            "overlapping_sources": [],
            "elapsed_seconds": round(elapsed, 3),
            "observations_per_second": round(accepted / elapsed, 1) if elapsed else 0,
            "chunk_seconds": chunk_seconds,
            "tracemalloc_peak_mib": round(peak / (1024 * 1024), 2),
            "sql_counts": dict(sorted(counts.items())),
            "note": "interleaved single-process streams (SQLite serializes writers)",
        }
    elif shape == "reprocess":
        sources = _seed(ws)
        first = _ingest_chunks(ws, chunks, sources=sources)
        second = _ingest_chunks(ws, chunks, sources=sources)
        result = {
            "first_run": {k: v for k, v in first.items() if k != "chunk_seconds"},
            "replay": {k: v for k, v in second.items() if k != "chunk_seconds"},
            "accepted": second["accepted"],
            "quarantined": second["quarantined"],
            "skipped_duplicates": second["skipped_duplicates"],
            "overlapping_sources": second["overlapping_sources"],
            "elapsed_seconds": second["elapsed_seconds"],
            "observations_per_second": second["observations_per_second"],
            "chunk_seconds": second["chunk_seconds"],
            "tracemalloc_peak_mib": second["tracemalloc_peak_mib"],
            "sql_counts": second["sql_counts"],
        }
    else:
        sources = _seed(ws)
        result = _ingest_chunks(ws, chunks, sources=sources, policy=policy)

    result.update(_db_stats(ws))
    return {
        "shape": shape,
        "description": description,
        "n": n,
        "chunk": eff_chunk,
        "dataset": str(dataset_path),
        "dataset_sha256": digest,
        **result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shape", choices=[*SHAPES, "all"], default="all")
    parser.add_argument("--n", type=int, default=10_000)
    parser.add_argument("--chunk", type=int, default=5_000)
    parser.add_argument("--windows", type=int, default=1440,
                        help="distinct 1-min windows for the dispersed shape")
    parser.add_argument("--sources", type=int, default=4)
    parser.add_argument("--ops-high-card", type=int, default=2000)
    parser.add_argument("--large-max-mib", type=int, default=64)
    parser.add_argument("--datasets", type=Path,
                        default=Path("lab/bench/datasets"))
    parser.add_argument("--work", type=Path, default=None,
                        help="kept workspace root (default: temp dir)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    shapes = list(SHAPES) if args.shape == "all" else [args.shape]
    tmp: tempfile.TemporaryDirectory[str] | None = None
    work = args.work
    if work is None:
        tmp = tempfile.TemporaryDirectory(prefix="sentinel-benchmx-")
        work = Path(tmp.name)
    try:
        report: dict[str, Any] = {
            "bench": "bench_matrix",
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "params": {
                "n": args.n, "chunk": args.chunk, "windows": args.windows,
                "sources": args.sources, "ops_high_card": args.ops_high_card,
                "large_max_mib": args.large_max_mib,
            },
            "results": [
                run_shape(s, n=args.n, chunk=args.chunk, args=args,
                          work_root=work, datasets=args.datasets)
                for s in shapes
            ],
        }
        payload = json.dumps(report, indent=2, sort_keys=True)
        print(payload)  # noqa: T201
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(payload, encoding="utf-8")
    finally:
        if tmp is not None:
            tmp.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
