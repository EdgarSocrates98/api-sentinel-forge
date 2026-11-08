"""Metrics bounded context — windowed request/latency/error aggregations.

Domain-pure: imports only `api_sentinel.domain` (+ stdlib/pydantic). The
observation shape is a `Protocol` (`windows.ObservationLike`) so the context
never imports the ingestion or infrastructure layers.
"""

from api_sentinel.metrics.aggregates import (
    ErrorClassification,
    MergedBucket,
    RequestClass,
    WindowAggregate,
    canonical_cumulative,
    classify_request,
    compute_window_aggregates,
    counter_deltas,
    cumulative_at,
    merge_bucket_sets,
    quantile,
)
from api_sentinel.metrics.windows import (
    BucketLike,
    MeasurementLike,
    ObservationLike,
    TimeWindow,
    parse_duration_seconds,
)

__all__ = [
    "BucketLike",
    "ErrorClassification",
    "MeasurementLike",
    "MergedBucket",
    "ObservationLike",
    "RequestClass",
    "TimeWindow",
    "WindowAggregate",
    "canonical_cumulative",
    "classify_request",
    "compute_window_aggregates",
    "counter_deltas",
    "cumulative_at",
    "merge_bucket_sets",
    "parse_duration_seconds",
    "quantile",
]
