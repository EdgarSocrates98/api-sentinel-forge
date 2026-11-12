"""AWS collection orchestrator — preflight → discover → collect → ingest.

One `collect_source` drives both source kinds:

- `cloudwatch`: metric queries come from the source config
  (`queries_json`) and feed `CloudWatchCollector` directly.
- `api-gateway`: REST + HTTP APIs are discovered first
  (`discover_gateways`), then each API+stage is mapped to its
  `AWS/ApiGateway` metric queries (opt-in per-route detail is
  cost-bearing and bounded).

Emitted datapoints become `RuntimeObservationV1` JSONL lines, grouped
by period and ingested through the existing pipeline — dedup,
quarantine and quality assessment are reused, never forked. Every
cycle leaves a redacted receipt and an audit row; credentials and
full ARNs never touch disk.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import Field

from api_sentinel.application.ports import UnitOfWork
from api_sentinel.contracts.v1.models import RuntimeObservationV1
from api_sentinel.domain import (
    AuditEvent,
    Clock,
    IdFactory,
    IngestionCheckpoint,
    SentinelError,
    TelemetrySource,
    ValidationError,
)
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.infrastructure.security.redact import redact
from api_sentinel.ingestion import IngestionPipeline
from api_sentinel.ingestion.models import IngestionPolicy
from api_sentinel.integrations.aws.apigateway import (
    GatewayInventory,
    apigateway_metric_queries,
    discover_gateways,
)
from api_sentinel.integrations.aws.budgets import (
    AwsCollectBudget,
    BudgetMeter,
    JitteredBackoff,
)
from api_sentinel.integrations.aws.client import (
    AwsClientSet,
    AwsSessionConfig,
)
from api_sentinel.integrations.aws.cloudwatch import (
    CloudWatchCollector,
    MetricQuery,
)
from api_sentinel.integrations.aws.errors import aws_refused
from api_sentinel.integrations.aws.identities import (
    AwsIdentity,
    verify_identity,
)
from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)

AWS_SOURCE_KINDS = ("cloudwatch", "api-gateway")


class AwsCollectReceipt(FrozenModel):
    """One cycle's redacted record — masked scope, counters, outcome."""

    receipt_id: str
    kind: str = "aws-collect"
    source_id: str
    account_masked: str
    account_hash: str
    region: str
    window_start: datetime
    window_end: datetime
    outcome: str  # collected | partial | empty | refused | failed
    detail: str = ""
    calls: int = 0
    pages: int = 0
    datapoints: int = 0
    throttled_retries: int = 0
    retried_calls: int = 0
    bytes_processed: int = 0
    estimated_cost_usd: float = 0.0
    batch_ids: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    at: datetime


class AwsCollectReport(FrozenModel):
    """What the CLI reports — same honest counts as the receipt."""

    source_id: str
    kind: str
    outcome: str
    detail: str = ""
    account_masked: str
    account_hash: str
    region: str
    window_start: datetime
    window_end: datetime
    resumed_from: datetime | None = None
    requested_queries: int = 0
    answered_queries: int = 0
    observations: int = 0
    accepted: int = 0
    quarantined: int = 0
    skipped_duplicates: int = 0
    batch_ids: list[str] = Field(default_factory=list)
    calls: int = 0
    pages: int = 0
    datapoints: int = 0
    throttled_retries: int = 0
    bytes_processed: int = 0
    estimated_cost_usd: float = 0.0
    truncated_queries: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    checkpoint: str = ""
    receipt_path: str | None = None


def write_collect_receipt(receipts_dir: Path, receipt: AwsCollectReceipt) -> Path:
    """Persist `<receipts_dir>/<id>.aws.receipt.json` — redacted, atomic."""
    receipts_dir.mkdir(parents=True, exist_ok=True)
    masked = redact(receipt.model_dump_json())
    path = receipts_dir / f"{receipt.receipt_id}.aws.receipt.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(masked.text, encoding="utf-8")
    tmp.replace(path)
    return path


def _queries_from_config(config: dict[str, str]) -> list[MetricQuery]:
    """`queries_json` → MetricQuery list; malformed → named refusal."""
    raw = config.get("queries_json") or config.get("queries") or "[]"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise aws_refused(f"invalid queries_json in source config: {exc}") from exc
    if not isinstance(parsed, list):
        raise aws_refused("queries_json must be a JSON list of query objects")
    queries: list[MetricQuery] = []
    for i, entry in enumerate(parsed):
        try:
            queries.append(MetricQuery.model_validate(entry))
        except ValueError as exc:
            raise aws_refused(
                f"invalid metric query at index {i}: {exc}",
            ) from exc
    return queries


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _checkpoint_state(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _observations_to_jsonl(observations: list[RuntimeObservationV1]) -> bytes:
    """Deterministic JSONL: same window re-collected → same bytes → dedup."""
    ordered = sorted(
        observations,
        key=lambda o: (o.window.start.isoformat(), o.observation_id),
    )
    return "\n".join(o.model_dump_json(by_alias=True) for o in ordered).encode("utf-8")


@dataclass
class _CollectWork:
    """Assembled plan — queries + inventory unknowns for one cycle."""

    queries: list[MetricQuery]
    unknowns: list[str]
    inventory: GatewayInventory | None = None


class CollectAwsSource:
    """The use case: one source, one window, one budgeted cycle."""

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        ids: IdFactory,
        redactor: Callable[[str], str] | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._ids = ids
        self._redactor = redactor or (lambda t: redact(t).text)

    def execute(
        self,
        *,
        source: TelemetrySource,
        clients: AwsClientSet,
        budget: AwsCollectBudget,
        window_seconds: int,
        end: datetime | None = None,
        resume: bool = False,
        receipts_dir: Path | None = None,
        sleep: Callable[[float], None] | None = None,
        jitter: Callable[[], float] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> AwsCollectReport:
        """Run one cycle; refusal paths write the receipt then raise."""
        if source.kind not in AWS_SOURCE_KINDS:
            raise ValidationError(
                f"source kind {source.kind!r} is not an AWS source",
                details={"supported": list(AWS_SOURCE_KINDS)},
            )
        if not source.enabled:
            raise ValidationError(f"telemetry source is disabled: {source.source_id}")
        # credentials-shaped config keys are refused before any session
        # work — the environment holds credentials, never Sentinel config
        session_config = AwsSessionConfig.from_source_config(source.config)
        meter = BudgetMeter(budget, monotonic=monotonic or time.monotonic)
        backoff_kwargs: dict[str, Any] = {}
        if sleep is not None:
            backoff_kwargs["sleep"] = sleep
        if jitter is not None:
            backoff_kwargs["jitter"] = jitter
        backoff = JitteredBackoff(**backoff_kwargs)

        window_end = end or self._clock.now()
        window_start = window_end - timedelta(seconds=window_seconds)
        lookback_minutes = window_seconds / 60.0
        if lookback_minutes > budget.max_lookback_minutes:
            raise aws_refused(
                f"requested lookback {lookback_minutes:.0f}m exceeds "
                f"budget max_lookback_minutes={budget.max_lookback_minutes}",
                details={"knob": "max_lookback_minutes"},
            )

        resumed_from: datetime | None = None
        if resume:
            resumed_from = self._resume_start(source, window_start)
            if resumed_from is not None:
                window_start = resumed_from

        identity: AwsIdentity | None = None
        try:
            identity = verify_identity(
                clients.sts,
                region=clients.region,
                expected_account_id=session_config.expected_account_id,
                expected_region=session_config.region,
            )
            plan = self._plan(
                source=source,
                clients=clients,
                budget=budget,
                meter=meter,
                backoff=backoff,
            )
            plan_unknowns = plan.unknowns
            collector = CloudWatchCollector(
                clients.cloudwatch,
                budget=budget,
                meter=meter,
                backoff=backoff,
            )
            result = collector.collect(
                plan.queries,
                start=window_start,
                end=window_end,
                service_id=source.service_id,
                source_id=source.source_id,
                identity=identity,
            )
            collected_obs = result.observations
            ingest_stats = self._ingest(source, collected_obs)
            checkpoint = self._write_checkpoint(source, window_end, len(collected_obs))
            unknowns = plan_unknowns + result.unknowns + result.messages
            if not collected_obs:
                outcome = "empty"
                detail = "no datapoints returned for the window"
            elif unknowns or ingest_stats["quarantined"]:
                outcome = "partial"
                detail = "collection completed with named losses"
            else:
                outcome = "collected"
                detail = "window fully collected"
            report = AwsCollectReport(
                source_id=source.source_id,
                kind=source.kind,
                outcome=outcome,
                detail=detail,
                account_masked=identity.masked_account,
                account_hash=identity.account_digest,
                region=identity.region,
                window_start=window_start,
                window_end=window_end,
                resumed_from=resumed_from,
                requested_queries=result.requested_queries,
                answered_queries=result.answered_queries,
                observations=len(collected_obs),
                accepted=ingest_stats["accepted"],
                quarantined=ingest_stats["quarantined"],
                skipped_duplicates=ingest_stats["skipped_duplicates"],
                batch_ids=ingest_stats["batch_ids"],
                calls=meter.calls,
                pages=meter.pages,
                datapoints=meter.datapoints,
                throttled_retries=meter.throttled_retries,
                bytes_processed=meter.bytes_processed,
                estimated_cost_usd=meter.estimated_cost_usd,
                truncated_queries=result.truncated_queries,
                messages=result.messages,
                unknowns=unknowns,
                checkpoint=checkpoint,
            )
            self._audit(source, outcome, meter, detail)
            if receipts_dir is not None:
                path = self._receipt(
                    receipts_dir,
                    report,
                    identity,
                    meter,
                    outcome,
                    detail,
                    unknowns,
                )
                report = report.model_copy(update={"receipt_path": str(path)})
            return report
        except SentinelError as exc:
            outcome = _failure_outcome(exc)
            self._audit(source, outcome, meter, exc.message)
            if receipts_dir is not None:
                self._refusal_receipt(
                    receipts_dir,
                    source,
                    identity,
                    meter,
                    window_start,
                    window_end,
                    outcome,
                    f"{exc.code}: {exc.message}",
                )
            raise
        except Exception as exc:
            # unexpected failure — still audited + receipted, never silent
            self._audit(source, "failed", meter, str(exc))
            if receipts_dir is not None:
                self._refusal_receipt(
                    receipts_dir,
                    source,
                    identity,
                    meter,
                    window_start,
                    window_end,
                    "failed",
                    f"{type(exc).__name__}: {exc}",
                )
            raise

    # -- plan -----------------------------------------------------------------

    def _plan(
        self,
        *,
        source: TelemetrySource,
        clients: AwsClientSet,
        budget: AwsCollectBudget,
        meter: BudgetMeter,
        backoff: JitteredBackoff,
    ) -> _CollectWork:
        config = source.config
        if source.kind == "cloudwatch":
            queries = _queries_from_config(config)
            if not queries:
                raise aws_refused(
                    "cloudwatch source has no queries_json configured",
                )
            return _CollectWork(queries=queries, unknowns=[])
        # api-gateway: discovery first, then metric mapping
        detailed = _truthy(config.get("detailed_metrics"))
        period = int(config.get("period_seconds") or "300")
        inventory = discover_gateways(
            clients.apigateway,
            clients.apigatewayv2,
            meter=meter,
            backoff=backoff,
            budget=budget,
            detailed_metrics=detailed,
        )
        queries = apigateway_metric_queries(inventory, period_seconds=period)
        unknowns = list(inventory.unknowns)
        if detailed:
            unknowns.append(
                "detailed_metrics:per-route queries are opt-in and cost-bearing (docs/14)"
            )
        return _CollectWork(queries=queries, unknowns=unknowns, inventory=inventory)

    # -- ingest / checkpoint / audit / receipt ---------------------------------

    def _ingest(
        self,
        source: TelemetrySource,
        observations: list[RuntimeObservationV1],
    ) -> dict[str, Any]:
        """Group by period → one JSONL batch per group, pipeline policy
        aligned so period windows are regular, not flagged irregular."""
        by_period: dict[int, list[RuntimeObservationV1]] = {}
        for obs in observations:
            period = int((obs.window.end - obs.window.start).total_seconds())
            by_period.setdefault(period, []).append(obs)
        accepted = quarantined = skipped = 0
        batch_ids: list[str] = []
        for period, group in sorted(by_period.items()):
            content = _observations_to_jsonl(group)
            policy = IngestionPolicy(window_seconds=period)
            result = IngestionPipeline(
                self._uow,
                self._clock,
                self._ids,
                self._redactor,
                policy=policy,
            ).ingest(source=source, kind="jsonl", content=content)
            accepted += result.accepted
            quarantined += result.quarantined
            skipped += result.skipped_duplicates
            batch_ids.append(result.batch_id)
        return {
            "accepted": accepted,
            "quarantined": quarantined,
            "skipped_duplicates": skipped,
            "batch_ids": batch_ids,
        }

    def _resume_start(self, source: TelemetrySource, requested_start: datetime) -> datetime | None:
        """Checkpoint resume: never re-fetch a window already collected."""
        with self._uow as uow:
            cp = uow.checkpoints.get(source.source_id)
        state = _checkpoint_state(cp.checkpoint if cp else None)
        raw = state.get("last_window_end")
        if not isinstance(raw, str):
            return None
        try:
            last_end = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return last_end if last_end > requested_start else None

    def _write_checkpoint(
        self, source: TelemetrySource, window_end: datetime, collected: int
    ) -> str:
        payload = json.dumps(
            {
                "last_window_end": window_end.isoformat(),
                "collected": collected,
                "region_scope": "account+region bound to source config",
            },
            sort_keys=True,
        )
        with self._uow as uow:
            uow.checkpoints.put(
                IngestionCheckpoint(
                    source_id=source.source_id,
                    checkpoint=payload,
                    updated_at=self._clock.now(),
                )
            )
            uow.commit()
        return payload

    def _audit(
        self,
        source: TelemetrySource,
        outcome: str,
        meter: BudgetMeter,
        detail: str,
    ) -> None:
        with self._uow as uow:
            uow.audit.append(
                AuditEvent(
                    audit_id=self._ids.new("aud"),
                    at=self._clock.now(),
                    actor="cli",
                    action="aws.collect",
                    target_type="telemetry_source",
                    target_id=source.source_id,
                    details={
                        "outcome": outcome,
                        "calls": str(meter.calls),
                        "datapoints": str(meter.datapoints),
                        "throttled_retries": str(meter.throttled_retries),
                        "detail": detail[:500],
                    },
                )
            )
            uow.commit()

    def _receipt(
        self,
        receipts_dir: Path,
        report: AwsCollectReport,
        identity: AwsIdentity,
        meter: BudgetMeter,
        outcome: str,
        detail: str,
        unknowns: list[str],
    ) -> Path:
        receipt = AwsCollectReceipt(
            receipt_id=self._ids.new("rcpt_aws"),
            source_id=report.source_id,
            account_masked=identity.masked_account,
            account_hash=identity.account_digest,
            region=identity.region,
            window_start=report.window_start,
            window_end=report.window_end,
            outcome=outcome,
            detail=detail,
            calls=meter.calls,
            pages=meter.pages,
            datapoints=meter.datapoints,
            throttled_retries=meter.throttled_retries,
            retried_calls=meter.retried_calls,
            bytes_processed=meter.bytes_processed,
            estimated_cost_usd=meter.estimated_cost_usd,
            batch_ids=report.batch_ids,
            unknowns=unknowns,
            at=self._clock.now(),
        )
        return write_collect_receipt(receipts_dir, receipt)

    def _refusal_receipt(
        self,
        receipts_dir: Path,
        source: TelemetrySource,
        identity: AwsIdentity | None,
        meter: BudgetMeter,
        window_start: datetime,
        window_end: datetime,
        outcome: str,
        detail: str,
    ) -> None:
        # when the STS preflight itself refused, account scope is unknown —
        # recorded as "unknown", never guessed
        receipt = AwsCollectReceipt(
            receipt_id=self._ids.new("rcpt_aws"),
            source_id=source.source_id,
            account_masked=(identity.masked_account if identity else "unknown"),
            account_hash=(identity.account_digest if identity else "unknown"),
            region=identity.region if identity else "unknown",
            window_start=window_start,
            window_end=window_end,
            outcome=outcome,
            detail=detail,
            calls=meter.calls,
            pages=meter.pages,
            datapoints=meter.datapoints,
            throttled_retries=meter.throttled_retries,
            retried_calls=meter.retried_calls,
            bytes_processed=meter.bytes_processed,
            estimated_cost_usd=meter.estimated_cost_usd,
            unknowns=[f"terminal:{outcome}"],
            at=self._clock.now(),
        )
        write_collect_receipt(receipts_dir, receipt)


def _failure_outcome(exc: SentinelError) -> str:
    if isinstance(exc, IntegrationRefused):
        return "refused"
    if isinstance(exc, IntegrationUnavailable):
        return "unavailable"
    return "failed"
