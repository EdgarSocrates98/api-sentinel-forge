"""Detection bounded context — deterministic detectors, honest anomalies.

Domain-pure (docs/08, ADR-007): detectors evaluate a typed request against a
typed context of read-only slices; no network, no injected-external clock,
no credentials, no implicit persistence. Anomaly identity is deterministic
so replay reproduces identical results.
"""

from api_sentinel.detection.baselines import (
    ewma,
    mad,
    median,
    summarize_baselines,
    window_ratio,
)
from api_sentinel.detection.evaluate import (
    EvaluationOutcome,
    RuleEvaluation,
    SuppressedCandidate,
    evaluate_rule,
    materialize_anomaly,
)
from api_sentinel.detection.protocol import (
    SEVERITY_RANK,
    Anomaly,
    AnomalyDraft,
    AnomalyStatus,
    DetectionContext,
    DetectionRequest,
    DetectionResult,
    Detector,
    Explanation,
    MetricBaseline,
    RuleRef,
    Severity,
    anomaly_id_for,
    detection_request_id,
    operation_ref,
)
from api_sentinel.detection.registry import (
    DetectionRule,
    DetectorRegistry,
    RulePolicy,
    default_rules,
)
from api_sentinel.detection.rules import DETECTOR_VERSION

__all__ = [
    "DETECTOR_VERSION",
    "SEVERITY_RANK",
    "Anomaly",
    "AnomalyDraft",
    "AnomalyStatus",
    "DetectionContext",
    "DetectionRequest",
    "DetectionResult",
    "DetectionRule",
    "Detector",
    "DetectorRegistry",
    "EvaluationOutcome",
    "Explanation",
    "MetricBaseline",
    "RuleEvaluation",
    "RulePolicy",
    "RuleRef",
    "Severity",
    "SuppressedCandidate",
    "anomaly_id_for",
    "default_rules",
    "detection_request_id",
    "evaluate_rule",
    "ewma",
    "mad",
    "materialize_anomaly",
    "median",
    "operation_ref",
    "summarize_baselines",
    "window_ratio",
]
