"""Reliability bounded context — SLI, versioned SLOs, error budgets, health.

Domain-pure: imports only `api_sentinel.domain` and `api_sentinel.metrics`
(+ stdlib/pydantic). No I/O; time is injected (`evaluated_at`/`now`).
"""

from api_sentinel.reliability.budget import (
    budget_consumed,
    budget_remaining,
    error_budget,
)
from api_sentinel.reliability.health import (
    HealthCondition,
    HealthPolicy,
    HealthState,
    ServiceHealthReport,
    SourceHealth,
    assess_service_health,
)
from api_sentinel.reliability.sli import SliKind, sli_value
from api_sentinel.reliability.slo import (
    RULE_ID,
    RULE_VERSION,
    AssessmentState,
    SloAssessment,
    SloCoverage,
    SloDefinition,
    SloThresholds,
    SourceAssessment,
    assessment_id_for,
    evaluate_slo,
)

__all__ = [
    "RULE_ID",
    "RULE_VERSION",
    "AssessmentState",
    "HealthCondition",
    "HealthPolicy",
    "HealthState",
    "ServiceHealthReport",
    "SliKind",
    "SloAssessment",
    "SloCoverage",
    "SloDefinition",
    "SloThresholds",
    "SourceAssessment",
    "SourceHealth",
    "assess_service_health",
    "assessment_id_for",
    "budget_consumed",
    "budget_remaining",
    "error_budget",
    "evaluate_slo",
    "sli_value",
]
