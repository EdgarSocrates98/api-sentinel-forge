"""Simulated AWS — the offline lab transport (LAB-013, tests).

`SimulatedAwsClient` implements the exact subset of the boto3 client
surface the adapter uses, per service (`sts`, `cloudwatch`,
`apigateway`, `apigatewayv2`). It answers from a canned per-operation
script, raises `SimulatedClientError` (botocore-`ClientError`-shaped)
for scripted failures like throttling, and records every invocation in
a shared `call_log` — the evidence that only allowlisted calls ever
reach the transport.

Nothing here performs real egress. The `--simulate` CLI flag and
`API_SENTINEL_AWS_SIMULATE` env var exist *only* for lab/test use.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeAlias

from api_sentinel.integrations.aws.client import AwsClientSet, ReadOnlyClient

LAB_ACCOUNT = "123456789012"
LAB_REGION = "us-east-1"

CannedEntry: TypeAlias = "dict[str, Any] | SimulatedClientError"
"""One queued response: a payload dict or an error to raise."""

ServiceScript: TypeAlias = Mapping[str, Iterable["CannedEntry"]]
"""service name → {operation → queued entries}"""


class SimulatedClientError(Exception):
    """Duck-typed botocore ClientError: `response["Error"]["Code"]`."""

    def __init__(self, code: str, message: str, operation: str) -> None:
        super().__init__(f"{code}: {message}")
        self.operation_name = operation
        self.response = {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        }


class SimulatedAwsClient:
    """A single-service stub — canned responses per operation, call log.

    `script` maps an operation name to an ordered queue of responses.
    Each entry is either a response dict (returned verbatim) or a
    `SimulatedClientError` (raised). An unscripted or exhausted
    operation raises `UnscriptedOperation` — the lab never invents data.
    """

    def __init__(
        self,
        service: str,
        *,
        script: Mapping[str, Iterable[CannedEntry]] | None = None,
        call_log: list[tuple[str, str, dict[str, Any]]] | None = None,
    ) -> None:
        self.service = service
        self._script = {op: deque(entries) for op, entries in (script or {}).items()}
        self.call_log = call_log if call_log is not None else []

    def _invoke(self, op: str, **kwargs: Any) -> Any:
        self.call_log.append((self.service, op, kwargs))
        queue = self._script.get(op)
        if not queue:
            raise SimulatedClientError(
                "UnscriptedOperation",
                f"simulated {self.service} has no canned response for {op}",
                op,
            )
        entry = queue.popleft()
        if isinstance(entry, SimulatedClientError):
            raise entry
        return entry

    # -- sts ---------------------------------------------------------------
    def get_caller_identity(self, **kwargs: Any) -> Any:
        return self._invoke("get_caller_identity", **kwargs)

    # -- cloudwatch ----------------------------------------------------------
    def get_metric_data(self, **kwargs: Any) -> Any:
        return self._invoke("get_metric_data", **kwargs)

    # -- apigateway (REST) ---------------------------------------------------
    def get_rest_apis(self, **kwargs: Any) -> Any:
        return self._invoke("get_rest_apis", **kwargs)

    def get_stages(self, **kwargs: Any) -> Any:
        return self._invoke("get_stages", **kwargs)

    def get_resources(self, **kwargs: Any) -> Any:
        return self._invoke("get_resources", **kwargs)

    # -- apigatewayv2 (HTTP) -------------------------------------------------
    def get_apis(self, **kwargs: Any) -> Any:
        return self._invoke("get_apis", **kwargs)

    def get_routes(self, **kwargs: Any) -> Any:
        return self._invoke("get_routes", **kwargs)


@dataclass
class SimulatedAws:
    """A simulated account boundary: the four read-only clients + call log."""

    clients: AwsClientSet
    call_log: list[tuple[str, str, dict[str, Any]]]


def simulated_clientset(
    *,
    script_by_service: Mapping[str, Mapping[str, Iterable[CannedEntry]]],
    region: str = LAB_REGION,
) -> SimulatedAws:
    """Wrap per-service stubs in ReadOnlyClient — same boundary as real."""
    call_log: list[tuple[str, str, dict[str, Any]]] = []

    def stub(service: str) -> SimulatedAwsClient:
        return SimulatedAwsClient(
            service,
            script=script_by_service.get(service),
            call_log=call_log,
        )

    return SimulatedAws(
        clients=AwsClientSet(
            sts=ReadOnlyClient(stub("sts"), service="sts"),
            cloudwatch=ReadOnlyClient(stub("cloudwatch"), service="cloudwatch"),
            apigateway=ReadOnlyClient(stub("apigateway"), service="apigateway"),
            apigatewayv2=ReadOnlyClient(stub("apigatewayv2"), service="apigatewayv2"),
            region=region,
        ),
        call_log=call_log,
    )


def caller_identity_response(
    account: str = LAB_ACCOUNT,
) -> dict[str, Any]:
    return {
        "Account": account,
        "Arn": f"arn:aws:iam::{account}:user/api-sentinel-lab",
        "UserId": "AIDASENTINELLAB000000",
    }


def metric_data_page(
    results: list[dict[str, Any]],
    *,
    marker: str | None = None,
    messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    page: dict[str, Any] = {"MetricDataResults": results}
    if marker is not None:
        page["NextToken"] = marker
    if messages:
        page["Messages"] = messages
    return page


def metric_data_result(
    query_id: str,
    timestamps: list[datetime],
    values: list[float],
    *,
    status: str = "CompleteData",
) -> dict[str, Any]:
    return {
        "Id": query_id,
        "Label": "lab-metric",
        "Timestamps": timestamps,
        "Values": values,
        "StatusCode": status,
    }


def lab013(
    *, now: datetime | None = None, account: str = LAB_ACCOUNT
) -> dict[str, dict[str, list[CannedEntry]]]:
    """LAB-013 script: throttling → backoff → paginated success + partial.

    cloudwatch:GetMetricData answers ThrottlingException twice, then
    page 1 (NextToken), then a final page carrying `Messages` — the
    receipt must show the throttled retries, both pages concatenated
    and the PARTIAL flag from the service message.
    """
    end = now or datetime.now(UTC)
    t1 = end - timedelta(minutes=15)
    t2 = end - timedelta(minutes=10)
    t3 = end - timedelta(minutes=5)
    return {
        "sts": {"get_caller_identity": [caller_identity_response(account)]},
        "cloudwatch": {
            "get_metric_data": [
                SimulatedClientError("ThrottlingException", "Rate exceeded", "GetMetricData"),
                SimulatedClientError("ThrottlingException", "Rate exceeded", "GetMetricData"),
                metric_data_page(
                    [
                        metric_data_result("q0", [t1, t2], [120.0, 240.0]),
                        metric_data_result("q1", [t1, t2], [4.0, 7.0]),
                    ],
                    marker="lab013-page-2",
                ),
                metric_data_page(
                    [
                        metric_data_result("q0", [t3], [310.0]),
                        metric_data_result("q1", [t3], [9.0]),
                    ],
                    messages=[
                        {
                            "Code": "PartialData",
                            "Value": "data points were omitted for part of the requested window",
                        }
                    ],
                ),
            ]
        },
        "apigateway": {
            "get_rest_apis": [{"items": [{"id": "restapi123", "name": "payments-api"}]}],
            "get_stages": [{"item": [{"stageName": "prod"}]}],
        },
        "apigatewayv2": {
            "get_apis": [
                {
                    "Items": [
                        {
                            "ApiId": "httpapi456",
                            "Name": "webhooks-api",
                            "ProtocolType": "HTTP",
                        }
                    ]
                }
            ],
            "get_stages": [{"Items": [{"StageName": "$default"}]}],
        },
    }


def throttle_forever(
    *, now: datetime | None = None, account: str = LAB_ACCOUNT
) -> dict[str, dict[str, list[CannedEntry]]]:
    """Every GetMetricData call throttles — the bounded-refusal path."""
    return {
        "sts": {"get_caller_identity": [caller_identity_response(account)]},
        "cloudwatch": {
            "get_metric_data": [
                SimulatedClientError("ThrottlingException", "Rate exceeded", "GetMetricData")
            ]
            * 10
        },
        "apigateway": {},
        "apigatewayv2": {},
    }


SIMULATED_SCENARIOS = {
    "lab013": lab013,
    "throttle-forever": throttle_forever,
}
