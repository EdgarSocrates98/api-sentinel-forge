"""Change intelligence — correlate incidents with `ChangeEvent`s honestly.

`correlate_changes` ranks changes inside a configurable temporal window
by *proximity* and *scope* — a deterministic, explainable score with its
reasons listed. It never claims causality: the report carries a
`proximity_ne_cause` disclaimer field, because temporal proximity alone
is not root cause (docs/09, spec AC).

`ImportDeployManifest` ingests a versioned `api-sentinel/DeployManifest/v1`
file — an exported pipeline manifest (GitHub Actions and friends) — into
`change_events` through `RecordChange`, so re-imports dedup by the
deterministic change id. No CI credential is ever required: the input
is a JSON file.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from api_sentinel.application.ports import UnitOfWork
from api_sentinel.contracts.v1 import DeployManifestV1, DeployRecord
from api_sentinel.domain.clock import FixedClock
from api_sentinel.domain.errors import (
    ContractError,
    NotFoundError,
    ValidationError,
)
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.models import ApiService, ChangeEvent, ChangeKind
from api_sentinel.incidents.models import Incident
from api_sentinel.infrastructure.security.paths import read_text_bounded
from api_sentinel.integrations.service import RecordChange, _change_id

#: The honesty field every correlation report carries — never omit it.
PROXIMITY_DISCLAIMER = (
    "proximity≠cause: temporal proximity alone is not root cause — "
    "independent evidence is required before any causal claim"
)

#: Explainable proximity buckets (seconds before the anchor) → weight.
_PROXIMITY_BUCKETS: list[tuple[float, float]] = [
    (300.0, 1.0),   # <= 5 min
    (900.0, 0.8),   # <= 15 min
    (1800.0, 0.6),  # <= 30 min
    (3600.0, 0.4),  # <= 60 min
]

#: Plausible-impact weight by change kind — a deploy/rollback/gateway
#: moves runtime behavior directly; a commit is indirect circumstantial
#: signal until shipped; maintenance is weakest.
_KIND_WEIGHT: dict[ChangeKind, float] = {
    ChangeKind.DEPLOY: 1.0,
    ChangeKind.ROLLBACK: 1.0,
    ChangeKind.GATEWAY: 0.9,
    ChangeKind.RELEASE: 0.9,
    ChangeKind.CONFIG: 0.8,
    ChangeKind.FLAG: 0.8,
    ChangeKind.AUTH_POLICY: 0.8,
    ChangeKind.CONTRACT: 0.7,
    ChangeKind.DEPENDENCY: 0.7,
    ChangeKind.COMMIT: 0.6,
    ChangeKind.MAINTENANCE: 0.5,
}


class CorrelatedChange(FrozenModel):
    """One ranked candidate — score + reasons, never a causal claim."""

    change: ChangeEvent
    delta_seconds: float  # anchor - change.at; positive = change precedes
    score: float
    reasons: list[str] = Field(default_factory=list)


class ChangeCorrelationReport(FrozenModel):
    """Ranked change candidates for an incident — honest correlation."""

    incident_id: str
    service_id: str
    anchor: datetime  # the instant correlated against (first_observed_at)
    window_seconds: int
    candidates: list[CorrelatedChange] = Field(default_factory=list)
    proximity_ne_cause: str = PROXIMITY_DISCLAIMER
    unknowns: list[str] = Field(default_factory=list)


def _proximity_weight(delta_seconds: float) -> tuple[float, str]:
    for limit, weight in _PROXIMITY_BUCKETS:
        if delta_seconds <= limit:
            return weight, f"within_{int(limit)}s"
    return 0.2, "within_window"


def correlate_changes(
    *,
    incident: Incident,
    changes: list[ChangeEvent],
    window_seconds: int = 3600,
) -> ChangeCorrelationReport:
    """Rank change events before the incident anchor — never causal.

    The anchor is `first_observed_at` (the earliest signal, not detection
    bookkeeping). Changes *after* the anchor cannot precede the symptom —
    they are excluded and named under `unknowns`, never ranked silently.
    Score = proximity_weight * kind_weight (+0.2 operation-scope bonus),
    sorted deterministically.
    """
    if window_seconds <= 0:
        raise ValidationError(
            "window_seconds must be positive",
            details={"window_seconds": window_seconds},
        )
    anchor = incident.first_observed_at
    candidates: list[CorrelatedChange] = []
    unknowns: list[str] = []
    after_anchor = 0
    for change in sorted(changes, key=lambda c: (c.at, c.change_id)):
        delta = (anchor - change.at).total_seconds()
        if delta < 0:
            after_anchor += 1
            continue
        if delta > window_seconds:
            continue
        proximity, proximity_reason = _proximity_weight(delta)
        kind_weight = _KIND_WEIGHT.get(change.kind, 0.5)
        reasons = [
            f"delta:{int(delta)}s_before_anchor",
            f"proximity:{proximity_reason}",
            f"kind:{change.kind.value}({kind_weight})",
            "scope:same_service",
        ]
        score = proximity * kind_weight
        if (
            incident.operation_id
            and change.details.get("operation_id") == incident.operation_id
        ):
            score += 0.2
            reasons.append("scope:operation_match(+0.2)")
        candidates.append(
            CorrelatedChange(
                change=change,
                delta_seconds=delta,
                score=round(score, 4),
                reasons=reasons,
            )
        )
    if after_anchor:
        unknowns.append(
            f"changes_after_anchor_excluded:{after_anchor} — a change "
            "after the first signal cannot precede it"
        )
    candidates.sort(
        key=lambda c: (-c.score, c.delta_seconds, c.change.change_id)
    )
    return ChangeCorrelationReport(
        incident_id=incident.incident_id,
        service_id=incident.service_id,
        anchor=anchor,
        window_seconds=window_seconds,
        candidates=candidates,
        unknowns=unknowns,
    )


# ---------------------------------------------------------------------------
# Deploy-manifest import — exported pipeline files, never CI credentials.


class ManifestImportReport(FrozenModel):
    """Honest import counters — dedup and skips are named, never silent."""

    file_ref: str
    imported: int = 0
    deduplicated: int = 0
    skipped: int = 0
    change_ids: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


def _resolve_service(raw: str, services: list[ApiService]) -> str | None:
    """`service` names a service_id directly, or a *unique* service_name —
    ambiguous or absent names return ``None``, never a name-guess."""
    by_id = {s.service_id for s in services}
    if raw in by_id:
        return raw
    matches = [s.service_id for s in services if s.service_name == raw]
    return matches[0] if len(matches) == 1 else None


class ImportDeployManifest:
    """`changes import --file` — versioned manifest → `change_events`.

    Unknown schemas are a `ContractError`; invalid records are skipped
    with an explicit unknown — never defaulted, never name-guessed.
    """

    def __init__(self, uow: UnitOfWork, now: datetime) -> None:
        self._uow, self._now = uow, now

    def execute(
        self,
        *,
        file: Path,
        source: str | None = None,
    ) -> ManifestImportReport:
        try:
            data = json.loads(
                read_text_bounded(file, name="deploy manifest")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError(
                f"deploy manifest unreadable: {file}",
                details={"error": type(exc).__name__},
            ) from exc
        if not isinstance(data, dict):
            raise ValidationError("deploy manifest must be a JSON object")
        schema = data.get("schema")
        if schema != DeployManifestV1.expected_schema:
            raise ContractError(
                f"unsupported schema {schema!r}, expected "
                f"'{DeployManifestV1.expected_schema}'",
                details={
                    "received": schema,
                    "expected": DeployManifestV1.expected_schema,
                },
            )
        try:
            manifest = DeployManifestV1.model_validate(data)
        except PydanticValidationError as exc:
            raise ContractError(
                f"deploy manifest invalid: {exc.error_count()} errors",
                details={"file": str(file)},
            ) from exc
        return self._import(
            manifest,
            file_ref=str(file),
            source=source or f"import:{file.name}",
        )

    def _import(
        self,
        manifest: DeployManifestV1,
        *,
        file_ref: str,
        source: str,
    ) -> ManifestImportReport:
        imported = 0
        deduplicated = 0
        skipped = 0
        change_ids: list[str] = []
        unknowns: list[str] = []
        record = RecordChange(self._uow, FixedClock(self._now))
        with self._uow as uow:
            services = uow.services.list()
        for rec in manifest.deploys:
            service_id = _resolve_service(rec.service, services)
            if service_id is None:
                skipped += 1
                unknowns.append(
                    f"service_unresolved:{rec.service} — no unique match; "
                    "never name-guessed"
                )
                continue
            try:
                kind = ChangeKind(rec.kind)
            except ValueError:
                skipped += 1
                unknowns.append(
                    f"kind_unsupported:{rec.kind} for {service_id} — "
                    "skipped, never defaulted"
                )
                continue
            known = self._known_change(service_id, kind, rec)
            details = dict(rec.details)
            details["exporter"] = manifest.exporter
            event = record.execute(
                service_id=service_id,
                kind=kind,
                at=rec.at,
                source=source,
                ref=rec.ref,
                details=details,
            )
            change_ids.append(event.change_id)
            if known is not None:
                deduplicated += 1
            else:
                imported += 1
        return ManifestImportReport(
            file_ref=file_ref,
            imported=imported,
            deduplicated=deduplicated,
            skipped=skipped,
            change_ids=change_ids,
            unknowns=sorted(unknowns),
        )

    def _known_change(
        self, service_id: str, kind: ChangeKind, rec: DeployRecord
    ) -> ChangeEvent | None:
        cid = _change_id(service_id, kind, rec.at, rec.ref)
        with self._uow as uow:
            return uow.change_events.get(cid)


class CorrelateIncidentChanges:
    """`changes correlate --incident-id` — ranked candidates + disclaimer."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self,
        *,
        incident_id: str,
        window_seconds: int = 3600,
    ) -> ChangeCorrelationReport:
        with self._uow as uow:
            incident = uow.incidents.get(incident_id)
            if incident is None:
                raise NotFoundError(f"incident not found: {incident_id}")
            changes = uow.change_events.list_for_service(
                incident.service_id
            )
        return correlate_changes(
            incident=incident,
            changes=changes,
            window_seconds=window_seconds,
        )
