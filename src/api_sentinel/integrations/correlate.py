"""Correlation — associate the two graphs by resolved identity + evidence.

The Sentinel owns the *operational* graph (`DependencyObservation`: calls,
failures, latencies actually seen). Code Atlas owns the *structural* graph
(relations found in code/contracts/IaC). Correlation never conflates them:
a structural edge is labeled `structural`, an observed edge `observed`, and
a hypothesis is `HYPOTHESIZED` until independent evidence verifies it —
temporal proximity is never root cause (docs/09, LAB-003 oracle).
"""

from __future__ import annotations

from pydantic import Field

from api_sentinel.detection.protocol import AnomalyStatus
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.incidents.models import ConfidenceClass


class CorrelatedEdge(FrozenModel):
    """One edge of the correlation report — graph kind is always explicit."""

    kind: str  # "observed" (Sentinel) | "structural" (Atlas) — never merged
    from_ref: str
    to_ref: str
    evidence: str = ""  # calls/errors for observed; resolver for structural
    provenance: str = ""  # observed | declared | inferred
    confidence: str = ""


class CorrelatedHypothesis(FrozenModel):
    """A candidate origin — confidence capped at HYPOTHESIZED (LAB-003)."""

    text: str
    confidence: ConfidenceClass = ConfidenceClass.HYPOTHESIZED
    evidence_refs: list[str] = Field(default_factory=list)
    caveat: str = (
        "temporal proximity and graph adjacency are not root cause — "
        "independent verification is required before VERIFIED"
    )


class CorrelationReport(FrozenModel):
    """An honest correlation: observed anomaly + dependency + context.

    `status` mirrors the anomaly ladder (OBSERVED..HYPOTHESIZED); it never
    reaches VERIFIED without independent evidence. `unknowns` records what
    could not be resolved — absent Atlas, unmatched identity, missing
    structural counterpart — never silently dropped.
    """

    service_id: str
    incident_id: str | None = None
    status: AnomalyStatus = AnomalyStatus.CORRELATED
    anomaly_ids: list[str] = Field(default_factory=list)
    observed_edges: list[CorrelatedEdge] = Field(default_factory=list)
    structural_edges: list[CorrelatedEdge] = Field(default_factory=list)
    suspected_origin: str | None = None
    hypotheses: list[CorrelatedHypothesis] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


def correlate_edges(
    *,
    service_id: str,
    anomaly_ids: list[str],
    observed: list[tuple[str, str, str]],
    structural: list[tuple[str, str, str]],
    suspected: str | None,
    unknowns: list[str],
    evidence_refs: list[str],
    incident_id: str | None = None,
) -> CorrelationReport:
    """Assemble a report; `suspected` names the observed dependency edge.

    `observed`/`structural` are ``(from_ref, to_ref, evidence)`` tuples
    already resolved by the caller — this function only labels and
    assembles; the two lists never merge.
    """
    hypotheses: list[CorrelatedHypothesis] = []
    if suspected is not None:
        hypotheses = [
            CorrelatedHypothesis(
                text=(
                    f"degradation is consistent with dependency {suspected}: "
                    "the anomaly attaches downstream of the observed edge; "
                    "structural context places it on the call path"
                ),
                evidence_refs=[*anomaly_ids, *evidence_refs],
            )
        ]
    return CorrelationReport(
        service_id=service_id,
        incident_id=incident_id,
        status=AnomalyStatus.CORRELATED if suspected else AnomalyStatus.OBSERVED,
        anomaly_ids=list(anomaly_ids),
        observed_edges=[
            CorrelatedEdge(kind="observed", from_ref=f, to_ref=t, evidence=e)
            for f, t, e in observed
        ],
        structural_edges=[
            CorrelatedEdge(
                kind="structural", from_ref=f, to_ref=t, evidence=e,
                provenance="declared",
            )
            for f, t, e in structural
        ],
        suspected_origin=suspected,
        hypotheses=hypotheses,
        unknowns=list(unknowns),
        evidence_refs=list(evidence_refs),
    )
