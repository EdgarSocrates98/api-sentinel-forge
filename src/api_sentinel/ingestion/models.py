"""Ingestion domain models — frozen, deterministic, dependency-free.

Pipeline data shapes:
- ImportedItem: one validated item after Acquire+Validate, before Normalize.
- NormalizedObservation: the canonical stored form (three clocks, quality).
- ImportOutcome: what an importer produced from one acquired payload.
- IngestionPolicy: configurable limits/windows (docs/05 processing policy).
- IngestResult: honest report of a pipeline run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import Field

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.quality import MeasurementKind, QualityState


class IngestionPolicy(FrozenModel):
    """Configurable processing policy (defaults per docs/05-telemetry-contracts)."""

    window_seconds: int = 60  # aggregation window, default 1 min
    lateness_seconds: int = 120  # tolerated event-time lag, default 2 min
    stale_after_seconds: int = 300  # window.end this far behind ingest → STALE
    max_batch_bytes: int = 10 * 1024 * 1024  # ~10 MB hard cap
    max_items: int = 100_000  # item-count hard cap
    max_quarantine_payload_bytes: int = 4096  # quarantine payloads stay bounded


class ObsBucket(FrozenModel):
    """One cumulative histogram bucket: count of observations ≤ le."""

    le: float | str  # "+Inf" for the terminal bucket
    count: int


class Measurement(FrozenModel):
    """A measurement carried through the pipeline (canonical after normalize)."""

    kind: MeasurementKind
    unit: str | None = None  # explicit only; normalized to a canonical unit
    value: float | None = None
    buckets: list[dict[str, Any]] = Field(default_factory=list)
    sum: float | None = None
    count: int | None = None
    temporality: str | None = None  # cumulative | delta — carried, never collapsed
    transform_notes: list[str] = Field(default_factory=list)


class NormalizedMeasurement(FrozenModel):
    """Canonical measurement: typed buckets, explicit canonical unit."""

    kind: MeasurementKind
    unit: str | None
    value: float | None = None
    buckets: list[ObsBucket] = Field(default_factory=list)
    sum: float | None = None
    count: int | None = None
    temporality: str | None = None
    transform_notes: list[str] = Field(default_factory=list)


class ImportedItem(FrozenModel):
    """One valid observation draft produced by an importer (pre-quality)."""

    observation_id: str
    service_id: str | None  # None → resolved against the ingest channel
    operation_id: str | None
    release_id: str | None
    window_start: datetime
    window_end: datetime
    declared_source_id: str | None  # envelope origin claim (validated vs channel)
    declared_quality: QualityState
    sampling_rate: float | None
    dropped: bool  # source reports dropped points/attributes
    issues: list[str] = Field(default_factory=list)  # structural issues → PARTIAL+
    measurement: Measurement
    attributes: dict[str, Any] = Field(default_factory=dict)
    transform_notes: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class QuarantineCandidate:
    """A payload that failed validation — reason is stable and machine-readable."""

    reason: str
    payload: str


class ImportOutcome(FrozenModel):
    items: list[ImportedItem]
    quarantine: list[QuarantineCandidate]
    total_items: int  # candidate items seen (for the max_items limit)
    checkpoint: str  # source-defined position (line count / byte offset)
    importer: str  # jsonl | otlp-json


class NormalizedObservation(FrozenModel):
    """Canonical persisted observation — three distinct clocks, never conflated."""

    observation_id: str
    service_id: str
    operation_id: str | None
    release_id: str | None
    window_start: datetime  # event time (interval start)
    window_end: datetime  # event time (interval end)
    ingest_time: datetime  # when the batch was received
    processing_time: datetime  # when this observation was persisted
    source_id: str
    batch_id: str
    quality: QualityState
    measurement: NormalizedMeasurement
    attributes: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str


class IngestResult(FrozenModel):
    """What the pipeline honestly did — never fabricates counts."""

    batch_id: str
    accepted: int
    quarantined: int
    skipped_duplicates: int
    checkpoint: str
    idempotency_key: str
    overlapping_sources: list[str] = Field(default_factory=list)
    aggregates: dict[str, Any] = Field(default_factory=dict)
