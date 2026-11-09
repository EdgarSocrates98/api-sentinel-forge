"""AWS read-only observability adapter (Cycle 7, ADR-013/ADR-014).

First remote connector: CloudWatch `GetMetricData` + API Gateway
discovery/metrics, wrapped in least-privilege credential handling and
per-source query budgets (docs/07, docs/14, blueprint §16.3/§22).

Hard rules honored here:
- Read-only *by construction*: every AWS call flows through
  `ReadOnlyClient`, an allowlist proxy (`get_*`/`list_*`/`describe_*`/
  `query`) that refuses anything else before it reaches the transport.
- Credentials live in the environment (profiles, SSO/Identity Center,
  temporary/workload creds) — never in source config, never persisted,
  never logged.
- Identity (STS `GetCallerIdentity`) is verified before any collection;
  account/region scope is explicit.
- Every egress is budgeted; exhaustion is a named terminal state or
  PARTIAL with unknowns — never silent truncation.
- The domain core never knows AWS exists: this adapter emits standard
  `RuntimeObservationV1` envelopes into the existing ingestion pipeline.
"""

from api_sentinel.integrations.aws.budgets import (
    AwsCollectBudget,
    BudgetMeter,
    JitteredBackoff,
)
from api_sentinel.integrations.aws.client import (
    AwsClientSet,
    AwsSessionConfig,
    ReadOnlyClient,
    boto3_available,
    is_allowed_operation,
    prove_read_only,
    real_clientset,
)
from api_sentinel.integrations.aws.errors import (
    AWS_FORGE,
    AwsBudgetExceeded,
    AwsThrottled,
    aws_refused,
    aws_unavailable,
)

__all__ = [
    "AWS_FORGE",
    "AwsBudgetExceeded",
    "AwsClientSet",
    "AwsCollectBudget",
    "AwsSessionConfig",
    "AwsThrottled",
    "BudgetMeter",
    "JitteredBackoff",
    "ReadOnlyClient",
    "aws_refused",
    "aws_unavailable",
    "boto3_available",
    "is_allowed_operation",
    "prove_read_only",
    "real_clientset",
]
