"""Install lifecycle state model — ``forge/InstallLifecycle/v1`` (Cycle 16.1 wave 12).

Receipt ``status`` is a coarse operation verdict — ``completed`` means the
writer finished, never "production-ready". The lifecycle state is the
finer-grained, honest position of the installation, *derived* from the
receipt/health evidence, never asserted by the writer.

Closed vocabulary (``forge/InstallLifecycle/v1``):

- ``planned``           — plan emitted (dry-run); nothing was written.
- ``blocked``           — a gate refused the operation: approval, unsupported
                          surface, lock, empty registry.
- ``failed``            — the operation ran and errored.
- ``applied``           — writes executed; verification absent or pending.
- ``partially_applied`` — writes executed for a subset only.
- ``verified``          — applied *and* receipt verification ``PASS``.
- ``unverified``        — no proof either way: no receipt, no ledger,
                          inconclusive evidence.
- ``ready``             — verified *and* health checks green.
- ``certified``         — ready *and* bound to external certification
                          evidence naming the tested SHA.

Transitions are informative, not enforced by the writer: honest evidence
gaps always degrade to ``unverified``. ``completed`` survives in
``status`` for backward compatibility — it is *not* a lifecycle state
and never implies readiness.

This module is a self-contained port of the shared contract semantics —
the forge interoperates over documents only and never imports The Forge.
"""

from __future__ import annotations

from typing import Any, Literal

LIFECYCLE_SCHEMA = "forge/InstallLifecycle/v1"

LifecycleState = Literal[
    "planned",
    "blocked",
    "failed",
    "applied",
    "partially_applied",
    "verified",
    "unverified",
    "ready",
    "certified",
]

STATES: tuple[str, ...] = (
    "planned",
    "blocked",
    "failed",
    "applied",
    "partially_applied",
    "verified",
    "unverified",
    "ready",
    "certified",
)
"""Closed state vocabulary — anything outside is a contract violation."""

TERMINAL_SUCCESS: frozenset[str] = frozenset({"verified", "ready", "certified"})
"""States that may back a success claim; ``applied`` alone never does."""

_BLOCKED_STATUSES = frozenset({"blocked", "refused", "unsupported"})
"""Receipt ``status`` values that mean a gate refused the operation."""

_GATED_CHECKS = frozenset({"BLOCKED", "UNVERIFIED", "NOT_APPLICABLE"})
"""Check statuses that mean "gated, not run" — a receipt made only of
these never applied anything."""


def receipt_state(doc: dict[str, Any]) -> tuple[LifecycleState, str]:
    """Derive the lifecycle state of a ``forge/InstallReceipt/v1`` doc.

    ``status`` + ``verification`` (+ per-forge rows on batch receipts) →
    one closed state. Existing receipts without ``lifecycle`` still
    parse — the state is a view, not a stored verdict.
    """
    status = str(doc.get("status") or "")
    verification = str((doc.get("verification") or {}).get("status") or "")
    if status == "planned":
        return "planned", "plan emitted — nothing written"
    if status in _BLOCKED_STATUSES:
        return "blocked", f"gate refusal: {status}"
    rows = doc.get("forges")
    if isinstance(rows, list) and rows:
        done = sum(1 for r in rows if isinstance(r, dict) and r.get("status") == "completed")
        if 0 < done < len(rows):
            return "partially_applied", f"{done}/{len(rows)} forges applied"
    if status == "completed":
        if verification == "PASS":
            return "verified", "writes executed and receipt checks pass"
        if verification == "FAIL":
            return "failed", "writes executed but verification failed"
        return "applied", f"writes executed; verification {verification or 'absent'}"
    if status == "failed":
        # An operation that never ran because every check hit a gate
        # (approval, unsupported, missing executable) is blocked, not
        # failed — the distinction the old binary vocabulary erased.
        checks = [c for c in doc.get("checks") or [] if isinstance(c, dict)]
        if checks and all(c.get("status") in _GATED_CHECKS for c in checks):
            return "blocked", "every check gated — nothing applied"
        return "failed", "operation errored"
    if status == "unverified" or not status:
        return "unverified", "no operation evidence"
    return "unverified", f"status {status!r} outside the known receipt vocabulary"


def health_state(doc: dict[str, Any]) -> tuple[LifecycleState, str]:
    """Derive the lifecycle state of a ``forge/InstallationHealth/v1``
    doc (``install status``/``doctor`` output)."""
    status = str(doc.get("status") or "")
    table: dict[str, tuple[LifecycleState, str]] = {
        "healthy": ("ready", "ledger intact — every managed path verified on disk"),
        "degraded": (
            "partially_applied",
            "drift on part of the managed set — repairable",
        ),
        "broken": ("failed", "managed state unrecoverable by repair"),
        "unverified": ("unverified", "no installation evidence at this scope"),
    }
    return table.get(status, ("unverified", f"health status {status!r} unknown"))


def block(state: LifecycleState, detail: str) -> dict[str, Any]:
    """The ``forge/InstallLifecycle/v1`` envelope added to receipts and
    health docs — additive, so older parsers keep working."""
    return {"schema": LIFECYCLE_SCHEMA, "state": state, "detail": detail}


def receipt_block(doc: dict[str, Any]) -> dict[str, Any]:
    """Derive + wrap the lifecycle block for a receipt-shaped doc."""
    state, detail = receipt_state(doc)
    return block(state, detail)


def health_block(doc: dict[str, Any]) -> dict[str, Any]:
    """Derive + wrap the lifecycle block for a health-shaped doc."""
    state, detail = health_state(doc)
    return block(state, detail)


__all__ = [
    "LIFECYCLE_SCHEMA",
    "STATES",
    "TERMINAL_SUCCESS",
    "LifecycleState",
    "block",
    "health_block",
    "health_state",
    "receipt_block",
    "receipt_state",
]
