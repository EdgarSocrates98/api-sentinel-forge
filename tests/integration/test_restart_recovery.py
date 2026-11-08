"""Cycle-1 gate: state survives process restart without loss or corruption."""

from pathlib import Path

from api_sentinel.application import ListServices, RegisterService, RegisterSource, StoreEvidence
from api_sentinel.domain import SequentialIdFactory, SystemClock
from api_sentinel.infrastructure.storage import FileBlobStore, SqliteUnitOfWork
from api_sentinel.workspace import ensure_workspace, resolve_workspace


def test_state_survives_restart(tmp_path: Path) -> None:
    ws = ensure_workspace(resolve_workspace(explicit=tmp_path / "ws"))
    clock, ids = SystemClock(), SequentialIdFactory()

    # "process" 1
    svc = RegisterService(SqliteUnitOfWork(ws.db_path), clock, ids).execute(
        service_name="payments-api"
    )
    src = RegisterSource(SqliteUnitOfWork(ws.db_path), clock, ids).execute(
        kind="jsonl-file", service_id=svc.service_id
    )
    ev = StoreEvidence(
        SqliteUnitOfWork(ws.db_path), clock, ids, FileBlobStore(ws.blobs_dir)
    ).execute(kind="metric-export", content=b"latency p95 sample", source_id=src.source_id)

    # "process" 2 — brand-new UnitOfWork instances over the same files
    services = ListServices(SqliteUnitOfWork(ws.db_path)).execute()
    assert [s.service_id for s in services] == [svc.service_id]
    with SqliteUnitOfWork(ws.db_path) as uow:
        assert uow.sources.get(src.source_id).kind == "jsonl-file"  # type: ignore[union-attr]
        assert uow.evidence.get(ev.evidence_id).hash == ev.hash  # type: ignore[union-attr]
    blob = FileBlobStore(ws.blobs_dir).get(ev.blob_ref)  # type: ignore[arg-type]
    assert blob == b"latency p95 sample"
