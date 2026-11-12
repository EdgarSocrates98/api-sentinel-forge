"""Ingestion scale benchmark — real measurements, never extrapolated claims.

Generates N `api-sentinel/RuntimeObservation/v1` JSONL observations, feeds
them through the real `IngestionPipeline` (parse → normalize → dedup →
persist) in bounded chunks, and measures:

- wall time per chunk and total;
- peak traced Python memory (`tracemalloc`);
- resulting `sentinel.db` size on disk.

Run:

    .venv/Scripts/python.exe lab/bench/bench_ingest.py --n 10000
    .venv/Scripts/python.exe lab/bench/bench_ingest.py --n 50000 --out bench.json

Numbers are recorded verbatim into `docs/cycles/cycle-09-report.md`. A slow
pytest (`tests/integration/test_bench_scale.py`, `-m slow`) wraps a modest N
so the bench stays regression-checked without lengthening the default suite.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path

from api_sentinel.domain import (
    ApiOperation,
    ApiService,
    OperationIdentity,
    ServiceEnvironment,
    SystemClock,
    TelemetrySource,
    UlidIdFactory,
)
from api_sentinel.infrastructure.security import redact
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion import IngestionPipeline
from api_sentinel.workspace import Workspace, ensure_workspace

SERVICE = "svc_bench"
SOURCE = "src_bench"
OPERATIONS = [f"op_bench_{i}" for i in range(8)]


def _observation(i: int, end: datetime) -> dict[str, object]:
    """One RuntimeObservationV1 line — a 1-minute latency histogram."""
    return {
        "schema": "api-sentinel/RuntimeObservation/v1",
        "observation_id": f"obs_bench_{i:07d}",
        "service_id": SERVICE,
        "operation_id": OPERATIONS[i % len(OPERATIONS)],
        "release_id": None,
        "window": {
            "start": (end - timedelta(seconds=60)).isoformat(),
            "end": end.isoformat(),
        },
        "source_id": SOURCE,
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
        "attributes": {
            "http.route": f"/bench/route{i % 8}",
            "http.request.method": "GET",
            "http.response.status_code": 200 if i % 23 else 503,
            "bench.seq": i,
        },
    }


def _seed(ws: Workspace) -> TelemetrySource:
    ensure_workspace(ws)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with SqliteUnitOfWork(ws.db_path) as uow:
        env = uow.environments.put(
            ServiceEnvironment(environment_id="env_bench", name="bench", created_at=now)
        )
        uow.services.put(
            ApiService(
                service_id=SERVICE,
                service_name="bench-api",
                environment_id=env.environment_id,
                created_at=now,
                updated_at=now,
            )
        )
        for op in OPERATIONS:
            uow.operations.put(
                ApiOperation.make(
                    operation_id=op,
                    service_id=SERVICE,
                    identity=OperationIdentity(protocol="http", method="GET", route=f"/bench/{op}"),
                    created_at=now,
                )
            )
        source = uow.sources.put(
            TelemetrySource(
                source_id=SOURCE,
                kind="jsonl-file",
                service_id=SERVICE,
                created_at=now,
            )
        )
        uow.commit()
    return source


def run_bench(
    *,
    n: int,
    chunk: int,
    workspace: Path,
) -> dict[str, object]:
    ws = ensure_workspace(Workspace(root=workspace, scope="project"))
    source = _seed(ws)
    clock = SystemClock()
    pipeline = IngestionPipeline(
        SqliteUnitOfWork(ws.db_path), clock, UlidIdFactory(), lambda t: redact(t).text
    )

    chunk_seconds: list[float] = []
    accepted = 0
    base = datetime.now(UTC)
    tracemalloc.start()
    started = time.perf_counter()
    offset = 0
    while offset < n:
        size = min(chunk, n - offset)
        # Realistic producer shape: each JSONL file covers a contiguous
        # time slice (chunk//100 distinct 1-minute windows), not the whole
        # corpus history — see cycle-09 report for the measured worst-case
        # path (every chunk spanning 1440 shared windows degrades the
        # per-batch overlap check to a backwards range scan per window).
        lines = [
            json.dumps(
                _observation(i, base - timedelta(minutes=offset // 100 + i % max(1, size // 100)))
            )
            for i in range(offset, offset + size)
        ]
        t0 = time.perf_counter()
        result = pipeline.ingest(source=source, kind="jsonl", content="\n".join(lines).encode())
        chunk_seconds.append(time.perf_counter() - t0)
        accepted += result.accepted
        offset += size
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    db_size = ws.db_path.stat().st_size
    wal = Path(str(ws.db_path) + "-wal")
    wal_size = wal.stat().st_size if wal.exists() else 0
    with SqliteUnitOfWork(ws.db_path) as uow:
        stored = uow.observations.count()

    return {
        "n": n,
        "chunk": chunk,
        "accepted": accepted,
        "stored": stored,
        "elapsed_seconds": round(elapsed, 3),
        "observations_per_second": round(accepted / elapsed, 1) if elapsed else 0,
        "chunk_seconds": [round(s, 3) for s in chunk_seconds],
        "tracemalloc_peak_mib": round(peak / (1024 * 1024), 2),
        "db_size_bytes": db_size,
        "db_size_mib": round(db_size / (1024 * 1024), 2),
        "wal_size_bytes": wal_size,
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=10_000)
    parser.add_argument("--chunk", type=int, default=5_000)
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if args.workspace is not None:
        report = run_bench(n=args.n, chunk=args.chunk, workspace=args.workspace)
    else:
        with tempfile.TemporaryDirectory(prefix="sentinel-bench-") as tmp:
            report = run_bench(n=args.n, chunk=args.chunk, workspace=Path(tmp) / "ws")
    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)  # noqa: T201
    if args.out is not None:
        args.out.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
