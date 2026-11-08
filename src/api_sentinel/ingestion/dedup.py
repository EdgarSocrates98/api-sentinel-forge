"""Deduplication — content-derived idempotency keys (reprocessing is a no-op)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def batch_idempotency_key(source_id: str, content: bytes) -> str:
    """One key per (source, payload): replaying the same file is a no-op."""
    digest = hashlib.sha256(b"batch\0" + source_id.encode("utf-8") + b"\0" + content)
    return digest.hexdigest()


def observation_idempotency_key(item: Any) -> str:
    """Content hash of one item — same observation in another batch still dedups."""
    canonical = json.dumps(
        item.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(b"obs\0" + canonical.encode("utf-8")).hexdigest()


def deterministic_observation_id(payload: Any) -> str:
    """Stable id for sources that don't assign one (OTLP data points)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "obs_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
