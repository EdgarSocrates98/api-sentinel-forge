"""Error budget math — exactly `B = N(1-S)` (docs/08).

- `B = N(1-S)` — requests allowed to fail inside the window (N eligible,
  S objective).
- `budget consumed = bad_requests / N(1-S)` — a fraction; > 1.0 means the
  budget is exhausted.
- `budget remaining = B - bad` — negative once the budget is overspent.

The formulas are only valid when numerator and denominator share population
definition, window and coverage; callers surface violations as explicit
assessment limitations rather than emitting a silently mixed ratio.
"""

from __future__ import annotations


def error_budget(eligible: float, target: float) -> float:
    """B = N(1-S) in requests. B == 0 whenever the eligible population is empty."""
    return eligible * (1.0 - target)


def budget_consumed(bad: float, eligible: float, target: float) -> float | None:
    """bad / N(1-S), or None when the budget itself is zero (undefined ratio —
    reported as a limitation, never silently coerced to infinity)."""
    budget = error_budget(eligible, target)
    if budget <= 0:
        return None
    return bad / budget


def budget_remaining(bad: float, eligible: float, target: float) -> float:
    """B - bad in requests; negative once overspent."""
    return error_budget(eligible, target) - bad
