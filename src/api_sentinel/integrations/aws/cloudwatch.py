"""CloudWatch `GetMetricData` collector → `RuntimeObservationV1` (§16.3).

Honesty rules (never assume one response covers the window):
- `NextToken` is followed with a per-call page cap; a capped page chain
  truncates that chunk and records the loss.
- `Messages` in a response mark the affected queries PARTIAL — the
  service told us the answer is incomplete.
- Budget knobs halt the cycle mid-way → collected data is ingested as
  PARTIAL with named unknowns (zero collected → bounded refusal).
- Units are explicit: a datapoint with no known unit is never assumed —
  it flows to the pipeline unlabeled and is flagged by normalization.
- `ScanBy=TimestampAscending` makes every page deterministic, so a
  re-collected window produces byte-identical JSONL and dedups cleanly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pydantic import Field

from api_sentinel.contracts.v1.models import (
    Measurement,
    RuntimeObservationV1,
    Window,
)
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.quality import MeasurementKind, QualityState
from api_sentinel.ingestion.dedup import deterministic_observation_id
from api_sentinel.integrations.aws.budgets import (
    AwsCollectBudget,
    BudgetMeter,
    JitteredBackoff,
    call_with_backoff,
)
from api_sentinel.integrations.aws.errors import AwsBudgetExceeded, aws_refused
from api_sentinel.integrations.aws.identities import AwsIdentity

# CloudWatch Unit → explicit unit token the normalizer understands
# (docs/05). "Seconds"→"s" is the deliberate s→ms conversion; unmapped
# units pass through raw so normalization flags them INVALID rather than
# assuming.
_CW_UNIT_MAP = {
    "Seconds": "s",
    "Milliseconds": "ms",
    "Microseconds": "us",
    "Count": "count",
    "Percent": "percent",
    "Bytes": "bytes",
}

_STAT_IS_PERCENTILE = re.compile(r"^p\d+(\.\d+)?$")


def _stat_kind(stat: str) -> MeasurementKind:
    """Sum/SampleCount → counter; aggregates/percentiles → gauge.

    A CloudWatch datapoint is already a period aggregate; Average/Min/
    Max/pNN are point estimates per window, never raw distributions.
    """
    if stat in ("Sum", "SampleCount"):
        return MeasurementKind.COUNTER
    return MeasurementKind.GAUGE


def _stat_unit(query_unit: str | None, stat: str) -> str | None:
    """Resolve the emitted unit token — explicit or honestly absent."""
    if query_unit is None:
        return "count" if stat in ("Sum", "SampleCount") else None
    return _CW_UNIT_MAP.get(query_unit, query_unit)


class MetricQuery(FrozenModel):
    """One `MetricDataQueries` entry plus Sentinel-side metadata.

    `attributes` ride onto every emitted observation (failure scope,
    api-gateway identity) so gateway-vs-app failures stay distinct.
    """

    namespace: str
    metric_name: str
    dimensions: dict[str, str] = Field(default_factory=dict)
    stat: str = "Average"
    period_seconds: int = 300
    unit: str | None = None  # CloudWatch Unit filter; None → service default
    attributes: dict[str, str] = Field(default_factory=dict)


@dataclass
class QuerySeries:
    """Per-query collection state."""

    query: MetricQuery
    datapoints: int = 0
    seen: bool = False
    truncated: bool = False
    messages: list[str] = field(default_factory=list)


@dataclass
class CloudWatchCollectResult:
    """What one cycle honestly produced — counts + unknowns, never padded."""

    observations: list[RuntimeObservationV1] = field(default_factory=list)
    requested_queries: int = 0
    answered_queries: int = 0
    datapoints: int = 0
    pages: int = 0
    calls: int = 0
    throttled_retries: int = 0
    bytes_processed: int = 0
    messages: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    truncated_queries: list[str] = field(default_factory=list)
    budget_exceeded: bool = False
    estimated_cost_usd: float = 0.0


def _metric_data_query(q: MetricQuery, query_id: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "Id": query_id,
        "ReturnData": True,
        "MetricStat": {
            "Metric": {
                "Namespace": q.namespace,
                "MetricName": q.metric_name,
                "Dimensions": [
                    {"Name": k, "Value": v}
                    for k, v in sorted(q.dimensions.items())
                ],
            },
            "Period": q.period_seconds,
            "Stat": q.stat,
        },
    }
    if q.unit is not None:
        entry["MetricStat"]["Unit"] = q.unit
    return entry


def datapoint_to_observation(
    *,
    query: MetricQuery,
    timestamp: datetime,
    value: float,
    service_id: str,
    source_id: str,
    identity: AwsIdentity,
    declared_quality: QualityState,
    transform_notes: list[str],
) -> RuntimeObservationV1:
    """One CloudWatch (stat, timestamp, value) → a RuntimeObservationV1.

    The observation id is deterministic over
    (source, namespace, metric, dims, stat, timestamp, period) so
    re-collecting a window dedups through the ingestion pipeline.
    """
    window_start = timestamp
    window_end = timestamp + timedelta(seconds=query.period_seconds)
    unit = _stat_unit(query.unit, query.stat)
    notes = list(transform_notes)
    if query.unit == "Seconds":
        notes.append("unit:CloudWatch Seconds→ms via normalize s→ms")
    observation_id = deterministic_observation_id(
        {
            "source_id": source_id,
            "namespace": query.namespace,
            "metric": query.metric_name,
            "dimensions": query.dimensions,
            "stat": query.stat,
            "ts": timestamp.isoformat(),
            "period": query.period_seconds,
        }
    )
    attributes: dict[str, Any] = {
        "cloud.provider": "aws",
        "aws.namespace": query.namespace,
        "aws.metric": query.metric_name,
        "aws.stat": query.stat,
        "aws.period_seconds": query.period_seconds,
        "aws.region": identity.region,
        "aws.account": identity.masked_account,
        "aws.account_hash": identity.account_digest,
    }
    for name, val in sorted(query.dimensions.items()):
        attributes[f"aws.dimension.{name}"] = val
    attributes.update(query.attributes)
    if unit is None:
        notes.append("unit:absent — flagged by normalization, never assumed")
    if notes:
        # recorded transform/provenance context — survives the JSONL
        # round-trip into NormalizedObservation attributes
        attributes["sentinel.transform_notes"] = "; ".join(notes)

    return RuntimeObservationV1(
        schema_=RuntimeObservationV1.expected_schema,
        observation_id=observation_id,
        service_id=service_id,
        operation_id=None,  # never synthesized — unmapped stays UNKNOWN
        release_id=None,
        window=Window(start=window_start, end=window_end),
        source_id=source_id,
        quality=declared_quality,
        measurement=Measurement(
            kind=_stat_kind(query.stat).value,
            unit=unit,
            value=value,
        ),
        attributes=attributes,
    )


class CloudWatchCollector:
    """Sequential GetMetricData collection under the cycle budget."""

    def __init__(
        self,
        client: Any,
        *,
        budget: AwsCollectBudget,
        meter: BudgetMeter | None = None,
        backoff: JitteredBackoff | None = None,
    ) -> None:
        self._client = client
        self._budget = budget
        self.meter = meter or BudgetMeter(budget)
        self._backoff = backoff or JitteredBackoff()

    def collect(
        self,
        queries: list[MetricQuery],
        *,
        start: datetime,
        end: datetime,
        service_id: str,
        source_id: str,
        identity: AwsIdentity,
    ) -> CloudWatchCollectResult:
        """Collect all queries over [start, end) — chunked, paged, budgeted."""
        result = CloudWatchCollectResult(requested_queries=len(queries))
        if not queries:
            result.unknowns.append("no_queries")
            return result
        self.meter.check_can_start()
        chunk_size = self._budget.max_metrics_per_query
        chunks = [
            queries[i : i + chunk_size]
            for i in range(0, len(queries), chunk_size)
        ]
        for chunk in chunks:
            try:
                self._collect_chunk(
                    chunk,
                    start=start,
                    end=end,
                    service_id=service_id,
                    source_id=source_id,
                    identity=identity,
                    result=result,
                )
            except AwsBudgetExceeded:
                # mid-collection budget hit → graceful halt, partial data
                # stays honest and named (never silent truncation)
                result.budget_exceeded = True
                result.unknowns.append(
                    f"budget_exceeded:{','.join(self.meter.exceeded)}"
                )
                break
            if self.meter.exceeded:
                result.unknowns.append(
                    f"budget_flagged:{','.join(self.meter.exceeded)}"
                )
                break
        self._finalize(result)
        return result

    def _collect_chunk(
        self,
        chunk: list[MetricQuery],
        *,
        start: datetime,
        end: datetime,
        service_id: str,
        source_id: str,
        identity: AwsIdentity,
        result: CloudWatchCollectResult,
    ) -> None:
        id_to_query = {
            f"q{i}": q for i, q in enumerate(chunk)
        }
        params: dict[str, Any] = {
            "MetricDataQueries": [
                _metric_data_query(q, qid) for qid, q in id_to_query.items()
            ],
            "StartTime": start,
            "EndTime": end,
            "ScanBy": "TimestampAscending",
        }
        series = {qid: QuerySeries(query=q) for qid, q in id_to_query.items()}
        next_token: str | None = None
        pages = 0
        while True:
            if next_token:
                params["NextToken"] = next_token
            elif "NextToken" in params:
                params.pop("NextToken")
            response = call_with_backoff(
                lambda: self._client.get_metric_data(**params),
                meter=self.meter,
                backoff=self._backoff,
                max_retries=self._budget.max_retries,
                metrics_requested=len(id_to_query),
                operation="cloudwatch:GetMetricData",
            )
            pages += 1
            n_dp = self._consume_page(
                response,
                series=series,
                id_to_query=id_to_query,
                service_id=service_id,
                source_id=source_id,
                identity=identity,
                result=result,
            )
            response_bytes = len(json.dumps(response, default=str))
            hit = self.meter.record_page(
                datapoints=n_dp, response_bytes=response_bytes
            )
            if hit is not None:
                # crossed a cap with this page → stop paging, declare loss
                for s in series.values():
                    if not s.truncated:
                        s.truncated = True
                result.unknowns.append(f"budget_cap_mid_page:{hit}")
                break
            next_token = response.get("NextToken")
            if not next_token:
                break
            if pages >= self._budget.max_pages_per_call:
                for s in series.values():
                    s.truncated = True
                result.unknowns.append(
                    f"page_cap:{self._budget.max_pages_per_call} pages per "
                    "GetMetricData chain — remainder not fetched"
                )
                break
        for s in series.values():
            if s.truncated:
                result.truncated_queries.append(
                    f"{s.query.namespace}/{s.query.metric_name}"
                )

    def _consume_page(
        self,
        response: dict[str, Any],
        *,
        series: dict[str, QuerySeries],
        id_to_query: dict[str, MetricQuery],
        service_id: str,
        source_id: str,
        identity: AwsIdentity,
        result: CloudWatchCollectResult,
    ) -> int:
        messages = [
            str(m.get("Value", ""))
            for m in response.get("Messages") or []
            if isinstance(m, dict)
        ]
        if messages:
            result.messages.extend(messages)
        page_points = 0
        for mdr in response.get("MetricDataResults") or []:
            qid = mdr.get("Id")
            query = id_to_query.get(str(qid))
            if query is None:
                result.unknowns.append(f"unmatched_result_id:{qid}")
                continue
            s = series[str(qid)]
            if not s.seen:
                s.seen = True
                result.answered_queries += 1
            if messages:
                s.messages.extend(messages)
            if str(mdr.get("StatusCode", "")).lower() not in {
                "completedata",
                "partialdata",
                "",
            }:
                s.truncated = True
                result.unknowns.append(
                    f"status:{mdr.get('StatusCode')} for {query.metric_name}"
                )
            timestamps = mdr.get("Timestamps") or []
            values = mdr.get("Values") or []
            for ts, value in zip(timestamps, values, strict=False):
                if self.meter.datapoints + page_points >= (
                    self._budget.max_datapoints
                ):
                    s.truncated = True
                    result.unknowns.append("datapoint_cap:series truncated")
                    continue
                ts_dt = ts if isinstance(ts, datetime) else self._parse_ts(ts)
                declared = (
                    QualityState.PARTIAL
                    if (s.truncated or s.messages)
                    else QualityState.COMPLETE
                )
                notes = [f"cloudwatch:GetMetricData page {self.meter.pages + 1}"]
                if s.messages:
                    notes.append(f"messages:{len(s.messages)}")
                obs = datapoint_to_observation(
                    query=query,
                    timestamp=ts_dt,
                    value=float(value),
                    service_id=service_id,
                    source_id=source_id,
                    identity=identity,
                    declared_quality=declared,
                    transform_notes=notes,
                )
                result.observations.append(obs)
                s.datapoints += 1
                page_points += 1
        return page_points

    @staticmethod
    def _parse_ts(raw: Any) -> datetime:
        if isinstance(raw, str):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        raise aws_refused(
            f"unparseable CloudWatch timestamp: {raw!r}",
            details={"raw_type": type(raw).__name__},
        )

    def _finalize(self, result: CloudWatchCollectResult) -> None:
        result.calls = self.meter.calls
        result.pages = self.meter.pages
        result.datapoints = self.meter.datapoints
        result.throttled_retries = self.meter.throttled_retries
        result.bytes_processed = self.meter.bytes_processed
        result.estimated_cost_usd = self.meter.estimated_cost_usd
