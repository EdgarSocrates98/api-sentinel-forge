"""Data Sentinel ACL — domain-triggered stale-data handoffs (Cycle 8).

Data Sentinel owns data-quality expertise; the Sentinel delegates a
*stale-data correlation* handoff only when an investigation actually
has staleness evidence — a domain trigger, never a log pipe.

Hard guard (docs/06, spec AC): the handoff payload is assembled from a
closed field set — service, staleness summary, evidence refs. Generic
HTTP/observation data is *structurally refused* (`_DENY_KEYS`): an HTTP
log line can never reach a Data Sentinel envelope — the adapter raises
`IntegrationRefused` before transport.

Transports mirror Doctor: emit the versioned
`api-sentinel/InvestigationHandoff/v1` envelope under `handoffs/` for a
separately-run Data Sentinel to consume; absence is `unavailable`,
never simulated.
"""

from __future__ import annotations

import hashlib
import importlib.util
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from api_sentinel.contracts.v1 import InvestigationHandoffV1
from api_sentinel.integrations.errors import IntegrationRefused

DATA_SENTINEL_FORGE = "data-sentinel-forge"
DATA_SENTINEL_MODULE = "data_sentinel"
STALE_DATA_KIND = "stale-data-correlation"

#: Payload keys that would smuggle generic telemetry into a domain
#: handoff — refused before transport, never silently dropped.
_DENY_KEYS = frozenset(
    {
        "logs",
        "log",
        "http_log",
        "http",
        "request_body",
        "response_body",
        "payload",
        "body",
        "headers",
        "trace",
        "span",
        "observations",
        "raw",
        "messages",
    }
)


def data_sentinel_importable() -> bool:
    """True iff `data_sentinel` resolves — never import-for-side-effect."""
    return importlib.util.find_spec(DATA_SENTINEL_MODULE) is not None


class DataSentinelMode(StrEnum):
    HANDOFF_FILE = "handoff_file"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DataSentinelResult:
    """What a stale-data handoff attempt produced — a named state."""

    handoff_id: str
    status: str  # delegated | refused | unavailable | failed
    request_ref: str | None = None
    detail: str = ""


def _validate_domain_payload(payload: dict[str, Any]) -> None:
    """The HTTP-log guard — refused before transport, by construction."""
    offenders = _DENY_KEYS & set(payload)
    if offenders:
        raise IntegrationRefused(
            DATA_SENTINEL_FORGE,
            "data sentinel handoffs carry domain fields only — generic "
            f"telemetry keys refused: {sorted(offenders)}",
        )
    staleness = payload.get("staleness")
    if isinstance(staleness, dict):
        nested = _DENY_KEYS & {str(k) for k in staleness}
        if nested:
            raise IntegrationRefused(
                DATA_SENTINEL_FORGE,
                "staleness summaries carry scalar domain fields only — "
                f"generic telemetry keys refused: {sorted(nested)}",
            )


class DataSentinelACL:
    """Stale-data delegation to Data Sentinel — handoff file or absent."""

    def __init__(
        self,
        mode: DataSentinelMode | str = DataSentinelMode.HANDOFF_FILE,
        *,
        handoffs_dir: Path | None = None,
    ) -> None:
        self._mode = DataSentinelMode(mode)
        self._dir = handoffs_dir

    @property
    def mode(self) -> DataSentinelMode:
        return self._mode

    def handoff_stale_data(
        self,
        *,
        service_id: str,
        staleness: dict[str, str],
        evidence_refs: list[str] | None = None,
        incident_id: str | None = None,
        at_iso: str = "",
    ) -> DataSentinelResult:
        """Delegate a *stale-data* correlation — domain fields only.

        `staleness` is a flat string summary (e.g. ``{"source": s,
        "last_seen": ts, "stale_seconds": n}``) — the allowlisted domain
        fields; `evidence_refs` cite evidence, never copy it.
        """
        handoff_id = "hnd_ds_" + hashlib.sha256(
            f"{service_id}|{incident_id or ''}|{at_iso}".encode()
        ).hexdigest()[:24]
        if not staleness:
            raise IntegrationRefused(
                DATA_SENTINEL_FORGE,
                "stale-data handoff requires a staleness summary — a "
                "domain trigger, not a log pipe",
            )
        payload: dict[str, Any] = {
            "service_id": service_id,
            "incident_id": incident_id or "",
            "staleness": dict(staleness),
        }
        _validate_domain_payload(payload)
        if self._mode is DataSentinelMode.UNAVAILABLE or self._dir is None:
            return DataSentinelResult(
                handoff_id=handoff_id,
                status="unavailable",
                detail=(
                    f"{DATA_SENTINEL_MODULE} not importable and no "
                    "handoffs transport configured — absent integrations "
                    "never simulate success"
                ),
            )
        try:
            envelope = InvestigationHandoffV1(
                schema_=InvestigationHandoffV1.expected_schema,
                handoff_id=handoff_id,
                from_forge="api-sentinel-forge",
                to_forge=DATA_SENTINEL_FORGE,
                kind=STALE_DATA_KIND,
                payload={**payload, "domain_trigger": "staleness"},
                constraints={
                    "evidence_refs": list(evidence_refs or []),
                    "scopes": ["read:data-quality"],
                },
            )
            slot = self._dir / handoff_id
            slot.mkdir(parents=True, exist_ok=True)
            path = slot / "stale_data_handoff.json"
            path.write_text(
                envelope.model_dump_json(indent=2, by_alias=True),
                encoding="utf-8",
            )
            return DataSentinelResult(
                handoff_id=handoff_id,
                status="delegated",
                request_ref=str(path),
                detail=(
                    "stale-data handoff emitted; a separately-run Data "
                    "Sentinel consumes the envelope — domain fields only, "
                    "no generic telemetry"
                ),
            )
        except OSError as exc:
            return DataSentinelResult(
                handoff_id=handoff_id,
                status="failed",
                detail=f"handoff could not be emitted: {exc}",
            )


def data_sentinel_from_config(
    config: dict[str, object], workspace_root: Path | None
) -> DataSentinelACL:
    """Resolve the adapter: `integrations.data_sentinel.mode` —
    `handoff_file` (default) or `unavailable`."""
    integrations = config.get("integrations") if isinstance(config, dict) else None
    ds = (
        integrations.get("data_sentinel")
        if isinstance(integrations, dict)
        else None
    )
    mode = (
        str(ds.get("mode", "handoff_file"))
        if isinstance(ds, dict)
        else "handoff_file"
    )
    handoffs_dir = (
        (workspace_root / "handoffs") if workspace_root is not None else None
    )
    if handoffs_dir is None and mode != "unavailable":
        mode = "unavailable"
    return DataSentinelACL(
        mode=DataSentinelMode(mode), handoffs_dir=handoffs_dir
    )
