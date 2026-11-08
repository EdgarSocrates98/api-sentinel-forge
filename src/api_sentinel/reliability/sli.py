"""Service-level indicators — `SLI = good / eligible` (docs/08).

An SLI is undefined when the eligible population is empty: `None` is
returned and the caller reports `INSUFFICIENT_DATA` — a missing denominator
never produces a fabricated 1.0.
"""

from __future__ import annotations

from enum import StrEnum


class SliKind(StrEnum):
    AVAILABILITY = "availability"  # good requests / eligible requests
    LATENCY = "latency"  # requests within threshold / eligible requests


def sli_value(good: float, eligible: float) -> float | None:
    """good/eligible, or None when eligible == 0 (undefined, never fabricated)."""
    if eligible <= 0:
        return None
    return good / eligible
