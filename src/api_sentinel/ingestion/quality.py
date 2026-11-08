"""Quality assessment — the worse of declared vs detected always wins.

Invariants: sampled data is labeled SAMPLED and never the inverse; PARTIAL,
STALE or SAMPLED never surface as COMPLETE; a declared non-COMPLETE state is
never upgraded on our word alone.

The severity order and `worst_of` combinator live in `domain.quality` so the
metrics context can reuse them without importing the ingestion context.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from api_sentinel.domain.quality import QualityState, worst_of, worst_quality

__all__ = ["assess_quality", "worst_of"]

# measurement issue tags that make the datum itself unusable
_INVALID_ISSUES = {"missing_unit", "unknown_unit", "bad_bucket", "invalid_kind"}


def assess_quality(
    *,
    declared: QualityState,
    sampling_rate: float | None,
    dropped: bool,
    measurement_issues: list[str],
    window_end: datetime,
    ingest_time: datetime,
    policy_stale_after_seconds: int,
    policy_lateness_seconds: int,
    temporality: str | None,
) -> tuple[QualityState, list[str]]:
    """Combine the producer's declared quality with what we detect.

    Returns (final_state, notes). The final state is the highest severity of
    declared and detected — never silently upgraded, never sampled by default.
    """
    detected = QualityState.COMPLETE
    notes: list[str] = []

    if any(i in _INVALID_ISSUES for i in measurement_issues):
        detected = QualityState.INVALID
        notes.append(f"invalid_measurement:{','.join(sorted(measurement_issues))}")
    elif measurement_issues:
        detected = worst_quality(detected, QualityState.PARTIAL)
        notes.append(f"measurement_issues:{','.join(sorted(measurement_issues))}")

    if (sampling_rate is not None and sampling_rate < 1.0) or dropped:
        detected = worst_quality(detected, QualityState.SAMPLED)
        if sampling_rate is not None:
            notes.append(f"sampling:rate={sampling_rate}")
        if dropped:
            notes.append("sampling:dropped_counts_reported")

    age = ingest_time - window_end
    if age > timedelta(seconds=policy_stale_after_seconds):
        detected = worst_quality(detected, QualityState.STALE)
        notes.append(f"stale:window_end {int(age.total_seconds())}s behind ingest")
    elif age > timedelta(seconds=policy_lateness_seconds):
        notes.append(f"late:window_end within tolerated lateness ({policy_lateness_seconds}s)")

    return worst_quality(declared, detected), notes
