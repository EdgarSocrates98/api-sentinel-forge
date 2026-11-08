"""Quarantine — failed payloads held apart: inspectable, bounded, never in aggregates."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.ids import IdFactory


class QuarantineEntry(FrozenModel):
    item_id: str
    batch_id: str
    reason: str
    payload: str  # redacted before persistence — never raw secrets
    payload_truncated: bool = False
    created_at: datetime


def make_entry(
    *,
    ids: IdFactory,
    batch_id: str,
    reason: str,
    payload: str,
    now: datetime,
    redactor: Callable[[str], str],
    max_payload_bytes: int,
) -> QuarantineEntry:
    """Build a bounded, redacted quarantine row; truncation is recorded."""
    cleaned = redactor(payload)
    encoded = cleaned.encode("utf-8", errors="replace")
    truncated = len(encoded) > max_payload_bytes
    if truncated:
        cleaned = encoded[:max_payload_bytes].decode("utf-8", errors="replace")
    return QuarantineEntry(
        item_id=ids.new("qit"),
        batch_id=batch_id,
        reason=reason,
        payload=cleaned,
        payload_truncated=truncated,
        created_at=now,
    )
