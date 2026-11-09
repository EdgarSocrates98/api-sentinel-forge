"""Delegation receipts — every attempted handoff leaves a redacted record.

Receipts carry: forge, request/handoff ids, outcome, timing and evidence
refs — never secrets, never payloads. The serialized JSON passes through
`security.redact` before touching disk; masked findings are recorded by
kind and count only.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import Field

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.infrastructure.security.redact import redact


class DelegationReceipt(FrozenModel):
    """What a delegation attempt honestly did — auditable, secret-free."""

    receipt_id: str
    to_forge: str
    kind: str
    outcome: str  # delegated | completed | refused | timed_out | unavailable | failed
    at: datetime
    handoff_id: str | None = None
    request_id: str | None = None
    duration_ms: float | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    detail: str = ""


def write_receipt(receipts_dir: Path, receipt: DelegationReceipt) -> Path:
    """Persist the receipt under `<receipts_dir>/<id>.receipt.json`, redacted."""
    receipts_dir.mkdir(parents=True, exist_ok=True)
    raw = receipt.model_dump_json()
    masked = redact(raw)
    path = receipts_dir / f"{receipt.receipt_id}.receipt.json"
    tmp = path.with_suffix(".receipt.json.tmp")
    tmp.write_text(masked.text, encoding="utf-8")
    tmp.replace(path)
    return path
