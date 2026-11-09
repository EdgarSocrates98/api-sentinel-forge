"""Ingestion pipeline — acquire → validate → normalize → dedup&assess → persist&aggregate.

Deterministic: clock/id/redactor injected; no network, no wall clock, no direct
file I/O (the CLI acquires bytes; the pipeline judges them).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from api_sentinel import __version__
from api_sentinel.domain import (
    ApiService,
    AuditEvent,
    Clock,
    IdFactory,
    IngestionCheckpoint,
    ObservationBatch,
    Provenance,
    QualityState,
    SignalType,
    TelemetrySource,
    ValidationError,
)
from api_sentinel.ingestion.dedup import (
    batch_idempotency_key,
    observation_idempotency_key,
)
from api_sentinel.ingestion.importers import detect_kind, parse_jsonl, parse_otlp_json
from api_sentinel.ingestion.models import (
    ImportedItem,
    IngestionPolicy,
    IngestResult,
    NormalizedObservation,
)
from api_sentinel.ingestion.normalize import normalize_measurement, redact_attributes
from api_sentinel.ingestion.quality import assess_quality, worst_of
from api_sentinel.ingestion.quarantine import QuarantineEntry, make_entry

if TYPE_CHECKING:
    from api_sentinel.application.ports import UnitOfWork

_KIND_SIGNAL = {
    "counter": SignalType.COUNTER,
    "gauge": SignalType.GAUGE,
    "histogram": SignalType.HISTOGRAM,
    "summary": SignalType.HISTOGRAM,
    "event": SignalType.LOG,
}


class IngestionPipeline:
    """Five-stage ingest over one acquired payload, one transaction."""

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        ids: IdFactory,
        redactor: Callable[[str], str],
        policy: IngestionPolicy | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._ids = ids
        self._redactor = redactor
        self._policy = policy or IngestionPolicy()

    def ingest(
        self, *, source: TelemetrySource, kind: str | None, content: bytes
    ) -> IngestResult:
        # --- Validate (size): a bounded refusal, never silent truncation ---
        if len(content) > self._policy.max_batch_bytes:
            raise ValidationError(
                f"batch exceeds max_batch_bytes ({len(content)} > "
                f"{self._policy.max_batch_bytes})",
                details={"max_batch_bytes": self._policy.max_batch_bytes},
            )
        importer = kind or detect_kind(content)
        if importer not in ("jsonl", "otlp-json"):
            raise ValidationError(
                f"unknown ingest kind: {importer!r}",
                details={"supported": ["jsonl", "otlp-json"]},
            )
        key = batch_idempotency_key(source.source_id, content)
        ingest_time = self._clock.now()

        with self._uow as uow:
            existing = uow.batches.get_by_idempotency_key(key)
            if existing is not None:
                # --- Dedup: the whole payload was processed before ---
                checkpoint = (
                    str(sum(1 for ln in content.split(b"\n") if ln.strip()))
                    if importer == "jsonl"
                    else str(len(content))
                )
                uow.checkpoints.put(
                    IngestionCheckpoint(
                        source_id=source.source_id,
                        checkpoint=checkpoint,
                        updated_at=self._clock.now(),
                    )
                )
                aggregates = uow.observations.aggregates()
                uow.commit()
                return IngestResult(
                    batch_id=existing.batch_id,
                    accepted=0,
                    quarantined=0,
                    skipped_duplicates=existing.item_count,
                    checkpoint=checkpoint,
                    idempotency_key=key,
                    aggregates=aggregates,
                )

            outcome = (
                parse_jsonl(content)
                if importer == "jsonl"
                else parse_otlp_json(
                    content, resolve_service=self._service_resolver(uow, source)
                )
            )
            if outcome.total_items > self._policy.max_items:
                raise ValidationError(
                    f"batch exceeds max_items ({outcome.total_items} > "
                    f"{self._policy.max_items})",
                    details={"max_items": self._policy.max_items},
                )

            # --- Normalize + Assess (reads only; writes deferred until batch exists) ---
            processing_time = self._clock.now()
            pending_obs: list[NormalizedObservation] = []
            pending_quarantine: list[QuarantineEntry] = []
            batch_id = self._ids.new("batch")
            seen_ids: dict[str, str] = {}  # observation_id → idempotency_key
            skipped = 0
            qualities: list[QualityState] = []
            overlap_windows: set[tuple[str, datetime, datetime]] = set()

            # C10-F006: repository-layer batching replaces three measured
            # per-item query loops (services.get per item, observations.get
            # per dedup candidate, overlapping_sources per window) with one
            # bounded read each. Semantics are identical — same rows, same
            # decisions; only the query shape changed.
            needed_service_ids = {
                item.service_id or source.service_id for item in outcome.items
            }
            known_services = uow.services.get_many(needed_service_ids)

            assessed: list[tuple[ImportedItem, NormalizedObservation]] = []
            for item in outcome.items:
                obs = self._assess_item(
                    known_services, source, item, batch_id,
                    ingest_time, processing_time,
                )
                if isinstance(obs, QuarantineEntry):
                    pending_quarantine.append(obs)
                else:
                    assessed.append((item, obs))

            # One batched dedup read for every candidate id: prior key by
            # observation_id, identical to per-row `get(...).idempotency_key`.
            prior_keys = (
                uow.observations.idempotency_keys(
                    [o.observation_id for _, o in assessed]
                )
                if assessed
                else {}
            )
            for item, obs in assessed:
                prior_key = seen_ids.get(obs.observation_id)
                if prior_key is None:
                    prior_key = prior_keys.get(obs.observation_id)
                if prior_key is not None:
                    if prior_key == obs.idempotency_key:
                        skipped += 1  # identical content redelivered → no-op
                    else:
                        # conflicting redelivery → quarantine, never a silent rewrite
                        pending_quarantine.append(
                            self._quarantine(
                                batch_id, "conflicting_observation_id",
                                item.model_dump_json(), ingest_time,
                            )
                        )
                    continue
                seen_ids[obs.observation_id] = obs.idempotency_key
                pending_obs.append(obs)
                qualities.append(obs.quality)
                overlap_windows.add(
                    (obs.service_id, obs.window_start, obs.window_end)
                )

            for qc in outcome.quarantine:
                pending_quarantine.append(
                    self._quarantine(batch_id, qc.reason, qc.payload, ingest_time)
                )

            # --- Persist & Aggregate (single transaction) ---
            windows_by_service: dict[str, set[tuple[datetime, datetime]]] = {}
            for service_id, w_start, w_end in overlap_windows:
                windows_by_service.setdefault(service_id, set()).add(
                    (w_start, w_end)
                )
            overlapping = sorted(
                {
                    src
                    for service_id, windows in windows_by_service.items()
                    for src in uow.observations.overlapping_sources_in_ranges(
                        service_id, windows,
                        exclude_source_id=source.source_id,
                    )
                }
            )
            signal_types = list(
                dict.fromkeys(
                    _KIND_SIGNAL[o.measurement.kind.value] for o in pending_obs
                )
            )
            notes = []
            if overlapping:
                notes.append(f"overlap:shared coverage with {','.join(overlapping)}")
            batch = ObservationBatch(
                batch_id=batch_id,
                provenance=Provenance(
                    source_id=source.source_id,
                    observed_at=ingest_time,
                    received_at=ingest_time,
                    adapter_version=f"{importer}-importer/{__version__}",
                    transform_notes="; ".join(notes) or None,
                ),
                signal_types=signal_types,
                quality=worst_of(qualities),
                item_count=outcome.total_items,
                idempotency_key=key,
            )
            uow.batches.put(batch)
            for obs in pending_obs:
                uow.observations.put(obs)
            for entry in pending_quarantine:
                uow.quarantine.put(entry)
            uow.checkpoints.put(
                IngestionCheckpoint(
                    source_id=source.source_id,
                    checkpoint=outcome.checkpoint,
                    updated_at=self._clock.now(),
                )
            )
            uow.audit.append(
                AuditEvent(
                    audit_id=self._ids.new("aud"),
                    at=self._clock.now(),
                    actor="cli",
                    action="ingestion.batch",
                    target_type="batch",
                    target_id=batch_id,
                    details={
                        "importer": importer,
                        "accepted": str(len(pending_obs)),
                        "quarantined": str(len(pending_quarantine)),
                        "skipped_duplicates": str(skipped),
                        **(
                            {"overlapping_sources": ",".join(overlapping)}
                            if overlapping
                            else {}
                        ),
                    },
                )
            )
            aggregates = uow.observations.aggregates()
            uow.commit()

        return IngestResult(
            batch_id=batch_id,
            accepted=len(pending_obs),
            quarantined=len(pending_quarantine),
            skipped_duplicates=skipped,
            checkpoint=outcome.checkpoint,
            idempotency_key=key,
            overlapping_sources=overlapping,
            aggregates=aggregates,
        )

    def _service_resolver(
        self, uow: UnitOfWork, source: TelemetrySource
    ) -> Callable[[str], str | None]:
        """OTLP `service.name` → registered service_id within the channel's
        environment. Unresolved names stay unresolved — never guessed.
        Results memoize per name: at most one lookup per distinct name."""
        channel = uow.services.get(source.service_id)
        env_id = channel.environment_id if channel is not None else None
        cache: dict[str, str | None] = {}

        def resolve(name: str) -> str | None:
            if env_id is None:
                return None
            if name not in cache:
                svc = uow.services.get_by_name(env_id, name)
                cache[name] = svc.service_id if svc is not None else None
            return cache[name]

        return resolve

    def _assess_item(
        self,
        known_services: dict[str, ApiService],
        source: TelemetrySource,
        item: ImportedItem,
        batch_id: str,
        ingest_time: datetime,
        processing_time: datetime,
    ) -> NormalizedObservation | QuarantineEntry:
        # --- Validate (origin): the envelope must speak for this channel ---
        if (
            item.declared_source_id is not None
            and item.declared_source_id != source.source_id
        ):
            return self._quarantine(
                batch_id, "origin_mismatch", item.model_dump_json(), ingest_time
            )
        service_id = item.service_id or source.service_id
        if service_id not in known_services:
            return self._quarantine(
                batch_id, "unresolved_service", item.model_dump_json(), ingest_time
            )

        # --- Normalize: explicit units, preserved kinds, redacted attributes ---
        measurement, issues = normalize_measurement(item.measurement)
        issues = issues + list(item.issues)
        attributes, redacted = redact_attributes(item.attributes, self._redactor)
        notes = list(item.transform_notes) + list(measurement.transform_notes)
        if redacted:
            notes.append(f"attributes_redacted:{redacted}")
        if (
            measurement.temporality != "cumulative"
            and item.window_end - item.window_start
            != timedelta(seconds=self._policy.window_seconds)
        ):
            issues.append("irregular_window")
            notes.append(f"window:duration!={self._policy.window_seconds}s")
        if notes:
            attributes["sentinel.transform_notes"] = notes

        quality, qnotes = assess_quality(
            declared=item.declared_quality,
            sampling_rate=item.sampling_rate,
            dropped=item.dropped,
            measurement_issues=issues,
            window_end=item.window_end,
            ingest_time=ingest_time,
            policy_stale_after_seconds=self._policy.stale_after_seconds,
            policy_lateness_seconds=self._policy.lateness_seconds,
            temporality=measurement.temporality,
        )
        if qnotes:
            attributes.setdefault("sentinel.quality_notes", qnotes)

        return NormalizedObservation(
            observation_id=item.observation_id,
            service_id=service_id,
            operation_id=item.operation_id,
            release_id=item.release_id,
            window_start=item.window_start,
            window_end=item.window_end,
            ingest_time=ingest_time,
            processing_time=processing_time,
            source_id=source.source_id,
            batch_id=batch_id,
            quality=quality,
            measurement=measurement,
            attributes=attributes,
            idempotency_key=observation_idempotency_key(item),
        )

    def _quarantine(
        self, batch_id: str, reason: str, payload: str, now: datetime
    ) -> QuarantineEntry:
        return make_entry(
            ids=self._ids,
            batch_id=batch_id,
            reason=reason,
            payload=payload,
            now=now,
            redactor=self._redactor,
            max_payload_bytes=self._policy.max_quarantine_payload_bytes,
        )
