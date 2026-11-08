"""Quality assessment — the worse of declared vs detected always wins.

Invariants: sampled data is labeled SAMPLED and never the inverse; PARTIAL,
STALE or SAMPLED never surface as COMPLETE; a declared non-COMPLETE state is
never upgraded on our word alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from api_sentinel.domain.quality import QualityState

_SEVERITY = {
    QualityState.COMPLETE: 0,
    QualityState.UNKNOWN: 1,
    QualityState.PARTIAL: 2,
    QualityState.STALE: 3,
    QualityState.SAMPLED: 4,
    QualityState.INVALID: 5,
}

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
        detected = _max(detected, QualityState.PARTIAL)
        notes.append(f"measurement_issues:{','.join(sorted(measurement_issues))}")

    if (sampling_rate is not None and sampling_rate < 1.0) or dropped:
        detected = _max(detected, QualityState.SAMPLED)
        if sampling_rate is not None:
            notes.append(f"sampling:rate={sampling_rate}")
        if dropped:
            notes.append("sampling:dropped_counts_reported")

    age = ingest_time - window_end
    if age > timedelta(seconds=policy_stale_after_seconds):
        detected = _max(detected, QualityState.STALE)
        notes.append(f"stale:window_end {int(age.total_seconds())}s behind ingest")
    elif age > timedelta(seconds=policy_lateness_seconds):
        notes.append(f"late:window_end within tolerated lateness ({policy_lateness_seconds}s)")

    return _max(declared, detected), notes


def _max(a: QualityState, b: QualityState) -> QualityState:
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


def worst_of(states: list[QualityState]) -> QualityState:
    """Batch-level quality = the worst item state (UNKNOWN when empty)."""
    if not states:
        return QualityState.UNKNOWN
    worst = states[0]
    for s in states[1:]:
        worst = _max(worst, s)
    return worst
