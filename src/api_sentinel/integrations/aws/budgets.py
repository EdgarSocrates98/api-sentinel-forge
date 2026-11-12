"""Per-source collection budgets (docs/14 cost model) + throttled backoff.

Every knob from the cost model is configurable: metrics per query,
datapoints, lookback window, collect frequency (caller's schedule),
calls per cycle, bytes processed, concurrency (fixed 1 — sequential
only), max runtime, retries, stored volume and an optional estimated
financial cap.

Enforcement semantics:
- a knob exhausted *before any work completes* → `AwsBudgetExceeded`
  (named bounded refusal);
- a knob hit *mid-collection* → the cycle halts and the result is
  PARTIAL with named unknowns — never silent truncation;
- throttling → jittered exponential backoff bounded by `max_retries`;
  exhaustion → `AwsThrottled` (named bounded refusal, LAB-013).
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import model_validator

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.integrations.aws.errors import (
    AwsBudgetExceeded,
    AwsThrottled,
    aws_refused,
)
from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)

# AWS-side throttle classifications that merit a bounded retry.
THROTTLE_CODES = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "ThrottledException",
        "TooManyRequestsException",
        "RequestThrottled",
        "RequestLimitExceeded",
        "Rate exceeded",
        "PriorRequestNotComplete",
        "SlowDown",
    }
)

# Rough CloudWatch pricing heuristic — $0.01 per 1,000 metrics requested
# via GetMetricData (documented estimate, never a billing figure).
COST_PER_1000_METRIC_QUERIES_USD = 0.01


def aws_error_code(exc: BaseException) -> str | None:
    """Extract a botocore-ClientError-shaped code (duck-typed)."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict):
            code = error.get("Code")
            return str(code) if code is not None else None
    return None


def aws_error_message(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict) and error.get("Message"):
            return str(error["Message"])
    return str(exc)


class AwsCollectBudget(FrozenModel):
    """Every egress knob from docs/14, with conservative defaults.

    `concurrency` is pinned to 1: collection is sequential by policy, so
    the field exists to be *checked*, not to enable parallelism later
    without an explicit change.
    """

    max_metrics_per_query: int = 100
    max_datapoints: int = 50_000
    max_lookback_minutes: int = 1_440  # 24 h retrospective cap
    max_calls_per_cycle: int = 60
    max_bytes_processed: int = 8 * 1024 * 1024
    concurrency: int = 1
    max_runtime_seconds: float = 120.0
    max_retries: int = 3
    max_pages_per_call: int = 10
    max_stored_bytes: int | None = None
    max_estimated_cost_usd: float | None = None
    max_detailed_routes_per_api: int = 25

    @model_validator(mode="after")
    def _check(self) -> AwsCollectBudget:
        positive = {
            "max_metrics_per_query": self.max_metrics_per_query,
            "max_datapoints": self.max_datapoints,
            "max_lookback_minutes": self.max_lookback_minutes,
            "max_calls_per_cycle": self.max_calls_per_cycle,
            "max_bytes_processed": self.max_bytes_processed,
            "max_pages_per_call": self.max_pages_per_call,
        }
        for name, value in positive.items():
            if value < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be > 0")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.concurrency != 1:
            raise ValueError("concurrency is pinned to 1 — sequential collection only")
        return self

    @classmethod
    def from_source_config(
        cls, config: dict[str, str], overrides: dict[str, Any] | None = None
    ) -> AwsCollectBudget:
        """Config dict → budget. Keys: `budget.<field>` (e.g.
        `budget.max_calls_per_cycle=25`). CLI `overrides` win last."""
        import json

        values: dict[str, Any] = {}
        raw_json = config.get("budget_json")
        if raw_json:
            try:
                parsed = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                raise aws_refused(
                    f"invalid budget_json in source config: {exc}",
                ) from exc
            if not isinstance(parsed, dict):
                raise aws_refused("budget_json must be a JSON object of fields")
            values.update(parsed)
        prefix = "budget."
        for key, value in config.items():
            if key.startswith(prefix):
                field_name = key[len(prefix) :]
                if field_name in cls.model_fields:
                    values[field_name] = value
        for key, value in (overrides or {}).items():
            if value is not None:
                values[key] = value
        try:
            return cls.model_validate(values)
        except ValueError as exc:
            raise aws_refused(f"invalid collection budget: {exc}") from exc


@dataclass
class BudgetMeter:
    """Runtime counters for one collection cycle.

    `exceeded` records which knobs fired — it becomes the PARTIAL
    unknowns. `metrics_requested` feeds the cost estimate.
    """

    budget: AwsCollectBudget
    monotonic: Callable[[], float] = time.monotonic
    started: float = field(init=False)
    calls: int = 0
    pages: int = 0
    datapoints: int = 0
    bytes_processed: int = 0
    throttled_retries: int = 0
    retried_calls: int = 0
    metrics_requested: int = 0
    exceeded: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.started = self.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        return self.monotonic() - self.started

    @property
    def estimated_cost_usd(self) -> float:
        return (self.metrics_requested / 1000.0) * COST_PER_1000_METRIC_QUERIES_USD

    def _flag(self, knob: str) -> None:
        if knob not in self.exceeded:
            self.exceeded.append(knob)

    def halt_reason(self) -> str | None:
        """The first exhausted knob, or None when egress may proceed."""
        if self.calls >= self.budget.max_calls_per_cycle:
            return "calls_per_cycle"
        if self.datapoints >= self.budget.max_datapoints:
            return "datapoints"
        if self.bytes_processed >= self.budget.max_bytes_processed:
            return "bytes_processed"
        if self.elapsed_seconds >= self.budget.max_runtime_seconds:
            return "max_runtime"
        if (
            self.budget.max_estimated_cost_usd is not None
            and self.estimated_cost_usd >= self.budget.max_estimated_cost_usd
        ):
            return "estimated_cost"
        return None

    def check_can_start(self) -> None:
        """Zero-work refusal: refuse outright when a knob is already spent."""
        reason = self.halt_reason()
        if reason is not None:
            raise AwsBudgetExceeded(
                f"collection budget already exhausted before first call: {reason}",
                details={
                    "knob": reason,
                    **{k: str(v) for k, v in self.summary().items()},
                },
            )

    def record_call(self, *, metrics_requested: int = 0) -> None:
        """Count one issued API call; raises when the cycle is spent."""
        self.calls += 1
        self.metrics_requested += metrics_requested
        reason = self.halt_reason()
        if reason is not None:
            self._flag(reason)
            raise AwsBudgetExceeded(
                f"collection budget exhausted mid-cycle: {reason}",
                details={"knob": reason},
            )

    def record_page(self, *, datapoints: int, response_bytes: int) -> str | None:
        """Count a response page; returns the knob newly hit, if any."""
        self.pages += 1
        self.datapoints += datapoints
        self.bytes_processed += response_bytes
        reason = self.halt_reason()
        if reason is not None:
            self._flag(reason)
        return reason

    def summary(self) -> dict[str, int | float]:
        return {
            "calls": self.calls,
            "pages": self.pages,
            "datapoints": self.datapoints,
            "bytes_processed": self.bytes_processed,
            "throttled_retries": self.throttled_retries,
            "retried_calls": self.retried_calls,
            "metrics_requested": self.metrics_requested,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


class JitteredBackoff:
    """Jittered exponential backoff — deterministic when `jitter` is seeded.

    delay(attempt) = min(cap, base * 2**attempt) + jitter() * base
    Injected `sleep` keeps tests/lab offline and wall-clock-free.
    """

    def __init__(
        self,
        *,
        base_seconds: float = 0.5,
        cap_seconds: float = 8.0,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._base: float = base_seconds
        self._cap: float = cap_seconds
        self._sleep: Callable[[float], None] = sleep
        self._jitter: Callable[[], float] = jitter

    def delay(self, attempt: int) -> float:
        expo = min(self._cap, self._base * (2.0**attempt))
        return expo + self._jitter() * self._base

    def wait(self, attempt: int) -> float:
        delay = self.delay(attempt)
        self._sleep(delay)
        return delay


T = TypeVar("T")


def call_with_backoff(
    fn: Callable[[], T],
    *,
    meter: BudgetMeter,
    backoff: JitteredBackoff,
    max_retries: int,
    metrics_requested: int = 0,
    operation: str,
) -> T:
    """One AWS call with bounded jittered retry — shared by collectors.

    Outcomes:
    - success → the response;
    - throttled beyond `max_retries` → `AwsThrottled` (named refusal,
      retries/throttle count observable on the meter → receipt);
    - any other AWS service error → `IntegrationRefused` (named);
    - non-AWS transport failure → `IntegrationUnavailable` (named).
    Budget exhaustion inside the attempt raises `AwsBudgetExceeded`.
    """
    attempt = 0
    while True:
        meter.record_call(metrics_requested=metrics_requested)
        try:
            return fn()
        except Exception as exc:  # classified below — never swallowed
            code = aws_error_code(exc)
            if code is not None and code in THROTTLE_CODES:
                meter.throttled_retries += 1
                if attempt >= max_retries:
                    raise AwsThrottled(
                        f"{operation} throttled past retry budget "
                        f"({max_retries} retries, code {code})",
                        details={
                            "operation": operation,
                            "aws_error": code,
                            "attempts": str(attempt + 1),
                        },
                    ) from exc
                meter.retried_calls += 1
                backoff.wait(attempt)
                attempt += 1
                continue
            if code is not None:
                raise IntegrationRefused(
                    "aws",
                    f"{operation} denied: {code} — {aws_error_message(exc)}",
                    details={"operation": operation, "aws_error": code},
                ) from exc
            raise IntegrationUnavailable(
                "aws",
                f"{operation} transport failure: {type(exc).__name__}: {exc}",
                details={"operation": operation},
            ) from exc
