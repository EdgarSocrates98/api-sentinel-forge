"""Detector protocol + typed request/context/result (docs/08, ADR-007).

The detection context is domain-pure: detectors receive a typed
`DetectionRequest` (scope + window + injected `now`) and a `DetectionContext`
(read-only data slices the application layer assembled via ports — never a
connection). Rules honored (docs/08 §Detector protocol):

- no network inside a detector; no uninjected real clock (`request.now` /
  `context.now` are the only time readings); no credentials; no implicit
  persistence — `evaluate` returns data, the caller persists;
- typed, serializable results carrying explicit evidence and limitations;
- deterministic: same request + same context ⇒ identical result (replay and
  detector-version evaluation come free).

Severity lives here (not in incidents) because every anomaly carries the
severity the *versioned rule config* declared — impact-driven policy, never
derived from HTTP status codes alone (docs/09).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.models import ServiceRelease, TelemetrySource
from api_sentinel.ingestion.models import NormalizedObservation
from api_sentinel.metrics.windows import TimeWindow
from api_sentinel.reliability.slo import SloAssessment


class Severity(StrEnum):
    """Impact-driven severity (docs/09) — never inferred from status codes."""

    SEV1 = "SEV1"  # critical business flow broadly unavailable
    SEV2 = "SEV2"  # significant degradation of a critical service
    SEV3 = "SEV3"  # partial or limited impact
    SEV4 = "SEV4"  # anomaly without proven relevant impact
    INFORMATIONAL = "INFORMATIONAL"  # observation for analysis, no incident


# Higher rank = worse operational impact; used only to *escalate* an open
# incident when a worse-severity anomaly attaches (recorded with rule revision).
SEVERITY_RANK = {
    Severity.INFORMATIONAL: 0,
    Severity.SEV4: 1,
    Severity.SEV3: 2,
    Severity.SEV2: 3,
    Severity.SEV1: 4,
}


class AnomalyStatus(StrEnum):
    """Confidence ladder for anomalies — distinct from workflow state.

    `OBSERVED → CORRELATED → HYPOTHESIZED → VERIFIED`; VERIFIED only via
    independent tool/procedure evidence. Level-1 rules emit OBSERVED only —
    correlation is Cycle-5 scope and temporal proximity is never root cause.
    """

    OBSERVED = "OBSERVED"
    CORRELATED = "CORRELATED"
    HYPOTHESIZED = "HYPOTHESIZED"
    VERIFIED = "VERIFIED"


class RuleRef(FrozenModel):
    """Which versioned rule + detector build produced a result."""

    rule_id: str
    rule_revision: int
    detector_id: str
    detector_version: str


class Explanation(FrozenModel):
    """Every alert explains the math that fired it (docs/08)."""

    summary: str
    math: str | None = None


class AnomalyDraft(FrozenModel):
    """What a detector emits before persistence/dedup — one detected deviation.

    `dedup_key` is the detector-chosen discriminator inside a rule+window
    scope (e.g. the operation scope or the silent source) so deterministic
    anomaly ids never collide across distinct deviations of one evaluation.
    """

    service_id: str
    operation_id: str | None = None
    operation_ref: str | None = None  # human label e.g. 'POST /payments/{id}'
    detected_at: datetime  # the injected `now`, never a wall clock
    window: TimeWindow
    severity: Severity
    explanation: Explanation
    evidence_refs: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    dedup_key: str = ""


class Anomaly(FrozenModel):
    """Persisted deviation (docs/03). Identity is deterministic:

    `anomaly_id` hashes rule+revision+scope+window+dedup_key, so replaying a
    detection run reproduces identical anomalies — persistence is idempotent
    by construction, never a duplicate.
    """

    anomaly_id: str
    service_id: str
    operation_id: str | None = None
    operation_ref: str | None = None
    detected_at: datetime
    window_start: datetime
    window_end: datetime
    rule: RuleRef
    severity: Severity
    status: AnomalyStatus = AnomalyStatus.OBSERVED
    explanation: Explanation
    evidence_refs: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    dedup_key: str = ""
    incident_id: str | None = None  # dedup link; set once via attach


def anomaly_id_for(
    *,
    rule_id: str,
    rule_revision: int,
    service_id: str,
    operation_id: str | None,
    dedup_key: str,
    window: TimeWindow,
) -> str:
    """Deterministic anomaly identity — replay reproduces the same id."""
    canonical = "|".join(
        (
            rule_id,
            str(rule_revision),
            service_id,
            operation_id or "",
            dedup_key,
            window.start.isoformat(),
            window.end.isoformat(),
        )
    )
    return "anm_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


class MetricBaseline(FrozenModel):
    """A simple baseline offered *as detector input* (docs/08 Level-2 methods).

    Computed from observed windows with an explicit method label. A baseline
    describes expected behavior — it never claims to identify root cause.
    """

    metric: str  # e.g. 'error_ratio' | 'latency_p95_ms' | 'request_rate'
    method: str  # e.g. 'median+mad' | 'ewma' | 'window_ratio'
    scope: str  # e.g. 'service' | 'operation:op_x' | 'source:src_y'
    value: float | None = None
    deviation: float | None = None  # MAD (or comparable scale) around value
    windows_used: int = 0
    notes: list[str] = Field(default_factory=list)


class DetectionRequest(FrozenModel):
    """One evaluation slot: scope + window + the injected clock reading."""

    request_id: str  # deterministic id of this evaluation (traceability)
    service_id: str
    window: TimeWindow
    now: datetime  # injected — detectors never read a real clock


class DetectionContext(FrozenModel):
    """Read-only slices the rules need — assembled via ports, never sqlite.

    The application layer fills exactly these slices; a detector cannot reach
    persistence, the network or a wall clock through this object.
    """

    service_id: str
    now: datetime
    observations: list[NormalizedObservation] = Field(default_factory=list)
    slo_assessments: list[SloAssessment] = Field(default_factory=list)
    sources: list[TelemetrySource] = Field(default_factory=list)
    releases: list[ServiceRelease] = Field(default_factory=list)
    recent_anomalies: list[Anomaly] = Field(default_factory=list)
    baselines: list[MetricBaseline] = Field(default_factory=list)


class DetectionResult(FrozenModel):
    """Typed detector output — anomalies plus explicit unknowns/limitations."""

    detector_id: str
    detector_version: str
    anomalies: list[AnomalyDraft] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def fired(self) -> bool:
        return bool(self.anomalies)


@runtime_checkable
class Detector(Protocol):
    """The docs/08 detector protocol — pure evaluate, versioned identity."""

    @property
    def detector_id(self) -> str: ...

    @property
    def detector_version(self) -> str: ...

    def evaluate(
        self, request: DetectionRequest, context: DetectionContext
    ) -> DetectionResult: ...


def detection_request_id(service_id: str, window: TimeWindow, now: datetime) -> str:
    """Deterministic id for one (service, window, now) evaluation slot."""
    canonical = (
        f"{service_id}|{window.start.isoformat()}|"
        f"{window.end.isoformat()}|{now.isoformat()}"
    )
    return "det_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def operation_ref(attributes: dict[str, Any], operation_id: str | None) -> str | None:
    """Human operation label: 'POST /payments/{id}', route, or operation_id."""
    method = attributes.get("http.request.method") or attributes.get("http.method")
    route = attributes.get("http.route") or attributes.get("url.path")
    if method and route:
        return f"{method} {route}"
    if route:
        return str(route)
    if method:
        return str(method)
    return operation_id
