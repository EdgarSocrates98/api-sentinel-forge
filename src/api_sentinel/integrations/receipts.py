"""Delegation receipts — every attempted handoff leaves a redacted record.

Receipts carry: forge, request/handoff/correlation ids, outcome, timing
and evidence refs — never secrets, never payloads. The serialized JSON
passes through `security.redact` before touching disk; masked findings
are recorded by kind and count only.

Cycle-10 split: `transport_state` records what the *transport* did
(emitted/delivered/not_sent/absent) while `execution_state` records what
the *remote forge* did (completed/timed_out/refused/not_executed) — a
delegated envelope on disk is never reported as executed work.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import Field

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.infrastructure.security.paths import safe_child_name
from api_sentinel.infrastructure.security.redact import redact

#: outcome → (transport_state, execution_state): the two axes a receipt
#: must keep distinct — "the envelope is on disk" is not "the forge ran".
_OUTCOME_SPLIT: dict[str, tuple[str, str]] = {
    "delegated": ("emitted", "pending"),
    "completed": ("delivered", "completed"),
    "timed_out": ("delivered", "timed_out"),
    "refused": ("not_sent", "refused"),
    "unavailable": ("absent", "not_executed"),
    "failed": ("unknown", "failed"),
}


def split_outcome(outcome: str) -> tuple[str, str]:
    """Map a delegation outcome to its (transport, execution) states."""
    return _OUTCOME_SPLIT.get(outcome, ("unknown", outcome))


class DelegationReceipt(FrozenModel):
    """What a delegation attempt honestly did — auditable, secret-free."""

    receipt_id: str
    to_forge: str
    kind: str
    outcome: str  # delegated | completed | refused | timed_out | unavailable | failed
    at: datetime
    handoff_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    transport_state: str = ""
    execution_state: str = ""
    duration_ms: float | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    detail: str = ""


def write_receipt(receipts_dir: Path, receipt: DelegationReceipt) -> Path:
    """Persist the receipt under `<receipts_dir>/<id>.receipt.json`, redacted.

    `receipt_id` becomes a filename — it passes through `safe_child_name`
    so a crafted id can never traverse out of the receipts root.
    """
    receipts_dir.mkdir(parents=True, exist_ok=True)
    raw = receipt.model_dump_json()
    masked = redact(raw)
    filename = f"{safe_child_name(receipt.receipt_id, name='receipt_id')}.receipt.json"
    path = receipts_dir / filename
    tmp = path.with_suffix(".receipt.json.tmp")
    tmp.write_text(masked.text, encoding="utf-8")
    tmp.replace(path)
    return path
