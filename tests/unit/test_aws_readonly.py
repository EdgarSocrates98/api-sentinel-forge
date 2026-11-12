"""Cycle 7 — AWS read-only observability: allowlist, budgets, throttling,
identity preflight, unit honesty, idempotent re-collection, receipt
redaction, and the REST-vs-HTTP API Gateway split.

Everything runs against `simulated.py` — zero egress, zero wall-clock
(injected sleep/jitter/monotonic).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from api_sentinel.domain import (
    ApiService,
    FixedClock,
    SequentialIdFactory,
    ServiceEnvironment,
    TelemetrySource,
)
from api_sentinel.domain.quality import QualityState
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.integrations.aws.apigateway import (
    apigateway_metric_queries,
    discover_gateways,
)
from api_sentinel.integrations.aws.budgets import (
    AwsCollectBudget,
    BudgetMeter,
    JitteredBackoff,
    call_with_backoff,
)
from api_sentinel.integrations.aws.client import (
    AwsSessionConfig,
    ReadOnlyClient,
    is_allowed_operation,
    prove_read_only,
)
from api_sentinel.integrations.aws.cloudwatch import (
    CloudWatchCollector,
    MetricQuery,
    datapoint_to_observation,
)
from api_sentinel.integrations.aws.collect import CollectAwsSource
from api_sentinel.integrations.aws.errors import (
    AwsBudgetExceeded,
    AwsThrottled,
)
from api_sentinel.integrations.aws.identities import (
    AwsIdentity,
    verify_identity,
)
from api_sentinel.integrations.aws.simulated import (
    LAB_ACCOUNT,
    LAB_REGION,
    SimulatedAwsClient,
    SimulatedClientError,
    caller_identity_response,
    lab013,
    metric_data_page,
    metric_data_result,
    simulated_clientset,
)
from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)
from api_sentinel.workspace import Workspace, ensure_workspace

NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)

NO_SLEEP = lambda _s: None  # noqa: E731
NO_JITTER = lambda: 0.0  # noqa: E731


# ---------------------------------------------------------------------------
# Read-only allowlist — the mechanical security boundary (ADR-013)
# ---------------------------------------------------------------------------


class _Wiretap:
    """Records every attribute the transport is asked for."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        def _call(**_kw: Any) -> dict[str, Any]:
            self.calls.append(name)
            return {"ok": True}

        return _call


class TestReadOnlyAllowlist:
    def test_proof_passes(self) -> None:
        passed, detail = prove_read_only()
        assert passed, detail

    @pytest.mark.parametrize(
        "name",
        [
            "terminate_instances",
            "put_metric_data",
            "delete_alarms",
            "create_api_key",
            "reboot_instances",
            "modify_instance_attribute",
        ],
    )
    def test_mutation_names_refused_pre_transport(self, name: str) -> None:
        tap = _Wiretap()
        ro = ReadOnlyClient(tap, service="ec2")
        with pytest.raises(IntegrationUnavailable):
            getattr(ro, name)
        assert tap.calls == []  # never reached the transport

    @pytest.mark.parametrize("name", ["get_paginator", "get_waiter"])
    def test_paginator_waiter_escape_hatches_refused(self, name: str) -> None:
        ro = ReadOnlyClient(_Wiretap(), service="cloudwatch")
        with pytest.raises(IntegrationUnavailable):
            getattr(ro, name)

    def test_mutation_with_pascal_case_refused(self) -> None:
        # boto3 method names are snake_case, but belt-and-braces: any
        # non-allowlisted attribute — whatever the spelling — is refused.
        ro = ReadOnlyClient(_Wiretap(), service="ec2")
        with pytest.raises(IntegrationUnavailable):
            _ = ro.TerminateInstances
        with pytest.raises(IntegrationUnavailable):
            _ = ro.PutMetricData

    def test_allowlisted_call_reaches_transport(self) -> None:
        tap = _Wiretap()
        ro = ReadOnlyClient(tap, service="cloudwatch")
        ro.get_metric_data(MetricDataQueries=[])
        ro.describe_alarms()
        ro.list_metrics()
        assert tap.calls == ["get_metric_data", "describe_alarms", "list_metrics"]

    def test_client_is_immutable(self) -> None:
        ro = ReadOnlyClient(_Wiretap(), service="sts")
        with pytest.raises(IntegrationUnavailable):
            ro.get_metric_data = lambda: None  # type: ignore[method-assign]

    def test_is_allowed_operation_predicate(self) -> None:
        assert is_allowed_operation("get_metric_data")
        assert is_allowed_operation("describe_alarms")
        assert is_allowed_operation("list_metrics")
        assert is_allowed_operation("query")
        assert not is_allowed_operation("put_metric_data")
        assert not is_allowed_operation("get_paginator")


class TestCredentialBoundary:
    def test_credential_shaped_config_refused(self) -> None:
        fake_key = "AKIAIOSFODNN7EXAMPLE"
        with pytest.raises(IntegrationRefused) as ei:
            AwsSessionConfig.from_source_config(
                {
                    "aws_access_key_id": fake_key,
                    "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                    "region": LAB_REGION,
                }
            )
        # the refusal names keys, never echoes their values
        assert fake_key not in str(ei.value)
        assert "wJalrXUtnFEMI" not in str(ei.value)
        refused_keys = str(ei.value.details.get("refused_keys", ""))
        assert "aws_access_key_id" in refused_keys
        assert fake_key not in refused_keys

    def test_scope_config_accepted(self) -> None:
        cfg = AwsSessionConfig.from_source_config(
            {
                "profile": "obs-readonly",
                "region": "eu-west-1",
                "account_id": "123456789012",
                "role_arn": "arn:aws:iam::123456789012:role/readonly",
            }
        )
        assert cfg.profile == "obs-readonly"
        assert cfg.region == "eu-west-1"
        assert cfg.expected_account_id == "123456789012"


# ---------------------------------------------------------------------------
# STS identity preflight
# ---------------------------------------------------------------------------


def _sts_stub(account: str = LAB_ACCOUNT) -> ReadOnlyClient:
    client = SimulatedAwsClient(
        "sts", script={"get_caller_identity": [caller_identity_response(account)]}
    )
    return ReadOnlyClient(client, service="sts")


class TestIdentityPreflight:
    def test_happy_path_returns_verified_identity(self) -> None:
        identity = verify_identity(_sts_stub(), region=LAB_REGION, expected_account_id=LAB_ACCOUNT)
        assert identity.account_id == LAB_ACCOUNT
        assert identity.masked_account == "1234****9012"
        assert identity.account_digest.startswith("sha256:")
        # the masked ARN never contains the full account id
        assert LAB_ACCOUNT not in identity.masked_arn

    def test_account_mismatch_is_named_refusal(self) -> None:
        with pytest.raises(IntegrationRefused) as ei:
            verify_identity(
                _sts_stub("999988887777"),
                region=LAB_REGION,
                expected_account_id=LAB_ACCOUNT,
            )
        assert "account mismatch" in str(ei.value)
        # masked — the full account id must not leak through the error
        assert "999988887777" not in str(ei.value)

    def test_region_mismatch_is_named_refusal(self) -> None:
        with pytest.raises(IntegrationRefused) as ei:
            verify_identity(
                _sts_stub(),
                region=LAB_REGION,
                expected_region="eu-west-1",
            )
        assert "region mismatch" in str(ei.value)

    def test_sts_denial_is_refused(self) -> None:
        stub = SimulatedAwsClient(
            "sts",
            script={
                "get_caller_identity": [
                    SimulatedClientError("AccessDenied", "denied", "GetCallerIdentity")
                ]
            },
        )
        with pytest.raises(IntegrationRefused):
            verify_identity(ReadOnlyClient(stub, service="sts"), region=LAB_REGION)

    def test_malformed_account_is_unavailable(self) -> None:
        stub = SimulatedAwsClient(
            "sts",
            script={
                "get_caller_identity": [{"Account": "not-an-account", "Arn": "", "UserId": ""}]
            },
        )
        with pytest.raises(IntegrationUnavailable):
            verify_identity(ReadOnlyClient(stub, service="sts"), region=LAB_REGION)


# ---------------------------------------------------------------------------
# Bounded jittered backoff
# ---------------------------------------------------------------------------


class TestBackoff:
    def _meter(self, **over: Any) -> BudgetMeter:
        return BudgetMeter(AwsCollectBudget(**over))

    def test_throttle_then_success_counts_retries_and_sleeps(self) -> None:
        sleeps: list[float] = []
        meter = self._meter()
        seq = iter(
            [
                SimulatedClientError("ThrottlingException", "x", "op"),
                {"MetricDataResults": []},
            ]
        )

        def fn() -> dict[str, Any]:
            item = next(seq)
            if isinstance(item, Exception):
                raise item
            return item

        out = call_with_backoff(
            fn,
            meter=meter,
            backoff=JitteredBackoff(sleep=sleeps.append, jitter=NO_JITTER),
            max_retries=3,
            operation="test:Op",
        )
        assert out == {"MetricDataResults": []}
        assert meter.throttled_retries == 1
        assert meter.retried_calls == 1
        assert meter.calls == 2
        assert sleeps == [0.5]  # base * 2^0 + 0 jitter

    def test_backoff_delay_is_jittered_exponential_and_capped(self) -> None:
        bo = JitteredBackoff(base_seconds=0.5, cap_seconds=1.0, sleep=NO_SLEEP, jitter=NO_JITTER)
        assert bo.delay(0) == 0.5
        assert bo.delay(1) == 1.0
        assert bo.delay(9) == 1.0  # capped
        jittered = JitteredBackoff(
            base_seconds=0.5,
            cap_seconds=8.0,
            sleep=NO_SLEEP,
            jitter=lambda: 1.0,
        )
        assert jittered.delay(0) == 1.0  # 0.5 expo + 0.5 jitter*base

    def test_throttling_exhaustion_raises_aws_throttled(self) -> None:
        meter = self._meter(max_retries=2)

        def always_throttle() -> dict[str, Any]:
            raise SimulatedClientError("ThrottlingException", "x", "op")

        with pytest.raises(AwsThrottled):
            call_with_backoff(
                always_throttle,
                meter=meter,
                backoff=JitteredBackoff(sleep=NO_SLEEP, jitter=NO_JITTER),
                max_retries=2,
                operation="cloudwatch:GetMetricData",
            )
        assert meter.throttled_retries == 3  # 1 initial + 2 retries
        assert meter.calls == 3

    def test_non_throttle_error_is_named_refusal(self) -> None:
        meter = self._meter()

        def denied() -> dict[str, Any]:
            raise SimulatedClientError("AccessDenied", "nope", "op")

        with pytest.raises(IntegrationRefused) as ei:
            call_with_backoff(
                denied,
                meter=meter,
                backoff=JitteredBackoff(sleep=NO_SLEEP, jitter=NO_JITTER),
                max_retries=3,
                operation="x",
            )
        assert "AccessDenied" in str(ei.value)

    def test_transport_failure_is_unavailable(self) -> None:
        meter = self._meter()

        def boom() -> dict[str, Any]:
            raise ConnectionError("socket exploded")

        with pytest.raises(IntegrationUnavailable):
            call_with_backoff(
                boom,
                meter=meter,
                backoff=JitteredBackoff(sleep=NO_SLEEP, jitter=NO_JITTER),
                max_retries=3,
                operation="x",
            )

    def test_zero_work_budget_refusal(self) -> None:
        meter = BudgetMeter(AwsCollectBudget(max_calls_per_cycle=1))
        meter.calls = 1  # a prior cycle spent the knob
        with pytest.raises(AwsBudgetExceeded):
            meter.check_can_start()


# ---------------------------------------------------------------------------
# CloudWatch collector — pagination, partials, budgets, units
# ---------------------------------------------------------------------------

_IDENTITY = AwsIdentity(
    account_id=LAB_ACCOUNT,
    arn=f"arn:aws:iam::{LAB_ACCOUNT}:user/lab",
    user_id="AIDALAB",
    region=LAB_REGION,
)

_Q = [
    MetricQuery(
        namespace="AWS/ApiGateway",
        metric_name="Latency",
        unit="Milliseconds",
        stat="Average",
    ),
    MetricQuery(
        namespace="AWS/ApiGateway",
        metric_name="5XXError",
        unit="Count",
        stat="Sum",
    ),
]


def _collector(
    script: list[Any], *, budget: AwsCollectBudget | None = None
) -> tuple[CloudWatchCollector, SimulatedAwsClient]:
    stub = SimulatedAwsClient("cloudwatch", script={"get_metric_data": script})
    coll = CloudWatchCollector(
        ReadOnlyClient(stub, service="cloudwatch"),
        budget=budget or AwsCollectBudget(),
        backoff=JitteredBackoff(sleep=NO_SLEEP, jitter=NO_JITTER),
    )
    return coll, stub


class TestCloudWatchCollector:
    def test_three_page_pagination_concatenated(self) -> None:
        t1, t2, t3 = (
            NOW - timedelta(minutes=15),
            NOW - timedelta(minutes=10),
            NOW - timedelta(minutes=5),
        )
        coll, _ = _collector(
            [
                metric_data_page([metric_data_result("q0", [t1], [1.0])], marker="p2"),
                metric_data_page([metric_data_result("q0", [t2], [2.0])], marker="p3"),
                metric_data_page([metric_data_result("q0", [t3], [3.0])]),
            ]
        )
        result = coll.collect(
            _Q[:1],
            start=NOW - timedelta(minutes=30),
            end=NOW,
            service_id="svc",
            source_id="src",
            identity=_IDENTITY,
        )
        assert result.pages == 3
        assert result.calls == 3
        assert result.datapoints == 3
        assert len(result.observations) == 3
        assert not result.truncated_queries
        assert not result.unknowns

    def test_page_cap_truncates_with_named_unknown(self) -> None:
        pages = [
            metric_data_page(
                [metric_data_result("q0", [NOW], [float(i)])],
                marker=f"page-{i}",
            )
            for i in range(5)
        ]
        coll, _ = _collector(pages, budget=AwsCollectBudget(max_pages_per_call=2))
        result = coll.collect(
            _Q[:1],
            start=NOW - timedelta(minutes=30),
            end=NOW,
            service_id="svc",
            source_id="src",
            identity=_IDENTITY,
        )
        assert result.pages == 2
        assert result.truncated_queries == ["AWS/ApiGateway/Latency"]
        assert any("page_cap" in u for u in result.unknowns)

    def test_response_messages_mark_partial(self) -> None:
        coll, _ = _collector(
            [
                metric_data_page(
                    [metric_data_result("q0", [NOW], [1.0])],
                    messages=[{"Code": "PartialData", "Value": "gaps"}],
                )
            ]
        )
        result = coll.collect(
            _Q[:1],
            start=NOW - timedelta(minutes=30),
            end=NOW,
            service_id="svc",
            source_id="src",
            identity=_IDENTITY,
        )
        assert result.messages == ["gaps"]
        assert result.observations[0].quality is QualityState.PARTIAL

    def test_datapoint_cap_marks_series_partial(self) -> None:
        ts = [NOW - timedelta(minutes=i) for i in range(5)]
        coll, _ = _collector(
            [metric_data_page([metric_data_result("q0", ts, [1.0] * 5)])],
            budget=AwsCollectBudget(max_datapoints=3),
        )
        result = coll.collect(
            _Q[:1],
            start=NOW - timedelta(hours=1),
            end=NOW,
            service_id="svc",
            source_id="src",
            identity=_IDENTITY,
        )
        assert result.datapoints == 3
        assert result.truncated_queries
        assert any("datapoint_cap" in u or "budget" in u for u in result.unknowns)

    def test_calls_budget_halts_mid_collection_as_partial(self) -> None:
        # one scripted page, but calls budget allows only 1 call and the
        # page carries a NextToken → the follow-up page cannot be fetched
        coll, _ = _collector(
            [
                metric_data_page([metric_data_result("q0", [NOW], [1.0])], marker="more"),
                metric_data_page([metric_data_result("q0", [NOW], [2.0])]),
            ],
            budget=AwsCollectBudget(max_calls_per_cycle=1),
        )
        result = coll.collect(
            _Q[:1],
            start=NOW - timedelta(minutes=30),
            end=NOW,
            service_id="svc",
            source_id="src",
            identity=_IDENTITY,
        )
        assert result.calls == 1
        assert result.budget_exceeded or result.truncated_queries
        assert result.unknowns

    def test_throttled_then_paginated_success(self) -> None:
        coll, _ = _collector(
            [
                SimulatedClientError("ThrottlingException", "Rate exceeded", "GetMetricData"),
                metric_data_page([metric_data_result("q0", [NOW], [7.0])]),
            ]
        )
        result = coll.collect(
            _Q[:1],
            start=NOW - timedelta(minutes=30),
            end=NOW,
            service_id="svc",
            source_id="src",
            identity=_IDENTITY,
        )
        assert result.throttled_retries == 1
        assert result.calls == 2
        assert result.datapoints == 1

    def test_queries_chunked_by_max_metrics_per_query(self) -> None:
        pages = [
            metric_data_page([metric_data_result("q0", [NOW], [1.0])]),
            metric_data_page([metric_data_result("q0", [NOW], [2.0])]),
        ]
        coll, stub = _collector(pages, budget=AwsCollectBudget(max_metrics_per_query=1))
        result = coll.collect(
            _Q,
            start=NOW - timedelta(minutes=30),
            end=NOW,
            service_id="svc",
            source_id="src",
            identity=_IDENTITY,
        )
        assert result.calls == 2
        for _service, _op, kwargs in stub.call_log:
            assert len(kwargs["MetricDataQueries"]) == 1


# ---------------------------------------------------------------------------
# Datapoint → observation: units + deterministic ids
# ---------------------------------------------------------------------------


class TestObservationMapping:
    def test_seconds_unit_maps_to_s_for_ms_normalization(self) -> None:
        obs = datapoint_to_observation(
            query=MetricQuery(
                namespace="AWS/Lambda",
                metric_name="Duration",
                stat="Average",
                unit="Seconds",
            ),
            timestamp=NOW,
            value=0.42,
            service_id="svc",
            source_id="src",
            identity=_IDENTITY,
            declared_quality=QualityState.COMPLETE,
            transform_notes=[],
        )
        # "s" is the explicit token the normalizer converts to ms x1000
        assert obs.measurement.unit == "s"
        notes = str(obs.attributes.get("sentinel.transform_notes") or "")
        assert "Seconds" in notes or "s→ms" in notes

    def test_absent_unit_stays_absent_and_noted(self) -> None:
        obs = datapoint_to_observation(
            query=MetricQuery(namespace="N", metric_name="M", stat="Average", unit=None),
            timestamp=NOW,
            value=1.0,
            service_id="svc",
            source_id="src",
            identity=_IDENTITY,
            declared_quality=QualityState.COMPLETE,
            transform_notes=[],
        )
        assert obs.measurement.unit is None

    def test_sum_without_unit_defaults_to_count(self) -> None:
        obs = datapoint_to_observation(
            query=MetricQuery(namespace="N", metric_name="M", stat="Sum", unit=None),
            timestamp=NOW,
            value=5.0,
            service_id="svc",
            source_id="src",
            identity=_IDENTITY,
            declared_quality=QualityState.COMPLETE,
            transform_notes=[],
        )
        assert obs.measurement.unit == "count"

    def test_observation_id_deterministic_across_collections(self) -> None:
        kw: dict[str, Any] = {
            "query": MetricQuery(
                namespace="N",
                metric_name="M",
                stat="Average",
                unit="Count",
                dimensions={"ApiName": "a"},
            ),
            "timestamp": NOW,
            "value": 1.0,
            "service_id": "svc",
            "source_id": "src",
            "identity": _IDENTITY,
            "declared_quality": QualityState.COMPLETE,
            "transform_notes": [],
        }
        assert (
            datapoint_to_observation(**kw).observation_id
            == datapoint_to_observation(**kw).observation_id
        )

    def test_full_account_never_in_attributes(self) -> None:
        obs = datapoint_to_observation(
            query=MetricQuery(namespace="N", metric_name="M"),
            timestamp=NOW,
            value=1.0,
            service_id="svc",
            source_id="src",
            identity=_IDENTITY,
            declared_quality=QualityState.COMPLETE,
            transform_notes=[],
        )
        blob = obs.model_dump_json()
        assert LAB_ACCOUNT not in blob
        assert "1234****9012" in blob


# ---------------------------------------------------------------------------
# API Gateway discovery — REST vs HTTP, failure scope, detailed opt-in
# ---------------------------------------------------------------------------


def _gw_stub(service: str, script: dict[str, list[Any]]) -> ReadOnlyClient:
    return ReadOnlyClient(SimulatedAwsClient(service, script=script), service=service)


class TestApiGateway:
    def _discover(
        self,
        rest_script: dict[str, list[Any]],
        http_script: dict[str, list[Any]],
        *,
        detailed: bool = False,
        budget: AwsCollectBudget | None = None,
    ):
        b = budget or AwsCollectBudget()
        meter = BudgetMeter(b)
        inv = discover_gateways(
            _gw_stub("apigateway", rest_script),
            _gw_stub("apigatewayv2", http_script),
            meter=meter,
            backoff=JitteredBackoff(sleep=NO_SLEEP, jitter=NO_JITTER),
            budget=b,
            detailed_metrics=detailed,
        )
        return inv, meter

    def test_rest_and_http_apis_stay_distinct(self) -> None:
        inv, _ = self._discover(
            {
                "get_rest_apis": [{"items": [{"id": "r1", "name": "orders"}]}],
                "get_stages": [{"item": [{"stageName": "prod"}]}],
            },
            {
                "get_apis": [
                    {
                        "Items": [
                            {
                                "ApiId": "h1",
                                "Name": "events",
                                "ProtocolType": "HTTP",
                            }
                        ]
                    }
                ],
                "get_stages": [{"Items": [{"StageName": "$default"}]}],
            },
        )
        types = {(e.api_id, e.api_type) for e in inv.endpoints}
        assert types == {("r1", "REST"), ("h1", "HTTP")}
        queries = apigateway_metric_queries(inv)
        rest_q = [q for q in queries if q.dimensions.get("ApiName")]
        http_q = [q for q in queries if q.dimensions.get("ApiId")]
        assert rest_q and http_q
        # REST spells errors 4XXError/5XXError; HTTP spells 4xx/5xx
        assert {q.metric_name for q in rest_q} >= {
            "Count",
            "4XXError",
            "5XXError",
            "Latency",
            "IntegrationLatency",
            "IntegrationError",
        }
        assert {q.metric_name for q in http_q} >= {
            "Count",
            "4xx",
            "5xx",
            "Latency",
            "IntegrationLatency",
            "IntegrationError",
        }
        # gateway-vs-app failure scope stays distinct on attributes
        scopes = {q.metric_name: q.attributes["aws.failure_scope"] for q in rest_q}
        assert scopes["5XXError"] == "gateway"
        assert scopes["IntegrationError"] == "integration"
        assert scopes["IntegrationLatency"] == "integration"

    def test_websocket_api_named_as_skipped_unknown(self) -> None:
        inv, _ = self._discover(
            {"get_rest_apis": [{"items": []}]},
            {
                "get_apis": [
                    {
                        "Items": [
                            {
                                "ApiId": "ws1",
                                "Name": "rt",
                                "ProtocolType": "WEBSOCKET",
                            }
                        ]
                    }
                ]
            },
        )
        assert not inv.endpoints
        assert any("websocket_api_skipped" in u for u in inv.unknowns)

    def test_rest_api_pagination_via_position(self) -> None:
        inv, _ = self._discover(
            {
                "get_rest_apis": [
                    {"items": [{"id": "r1", "name": "a"}], "position": "nxt"},
                    {"items": [{"id": "r2", "name": "b"}]},
                ],
                "get_stages": [
                    {"item": [{"stageName": "dev"}]},
                    {"item": [{"stageName": "dev"}]},
                ],
            },
            {"get_apis": [{"Items": []}]},
        )
        assert {e.api_id for e in inv.endpoints} == {"r1", "r2"}

    def test_detailed_routes_opt_in_and_bounded(self) -> None:
        items = [
            {
                "path": f"/r{i}",
                "resourceMethods": {"GET": {}, "POST": {}},
            }
            for i in range(30)
        ]
        inv, _ = self._discover(
            {
                "get_rest_apis": [{"items": [{"id": "r1", "name": "a"}]}],
                "get_stages": [{"item": [{"stageName": "prod"}]}],
                "get_resources": [{"items": items}],
            },
            {"get_apis": [{"Items": []}]},
            detailed=True,
            budget=AwsCollectBudget(max_detailed_routes_per_api=5),
        )
        assert len(inv.rest_routes["r1"]) == 5
        assert any("route_cap" in u for u in inv.unknowns)

    def test_no_detailed_metrics_means_no_route_calls(self) -> None:
        rest = SimulatedAwsClient(
            "apigateway",
            script={
                "get_rest_apis": [{"items": [{"id": "r1", "name": "a"}]}],
                "get_stages": [{"item": [{"stageName": "p"}]}],
            },
        )
        http = SimulatedAwsClient("apigatewayv2", script={"get_apis": [{"Items": []}]})
        b = AwsCollectBudget()
        discover_gateways(
            ReadOnlyClient(rest, service="apigateway"),
            ReadOnlyClient(http, service="apigatewayv2"),
            meter=BudgetMeter(b),
            backoff=JitteredBackoff(sleep=NO_SLEEP, jitter=NO_JITTER),
            budget=b,
            detailed_metrics=False,
        )
        ops = [op for _svc, op, _kw in rest.call_log]
        assert "get_resources" not in ops


# ---------------------------------------------------------------------------
# End-to-end: CollectAwsSource over the simulated account boundary
# ---------------------------------------------------------------------------


def _workspace(tmp_path: Path) -> tuple[Workspace, SqliteUnitOfWork]:
    ws = Workspace(root=tmp_path / "ws", scope="project")
    ensure_workspace(ws)
    uow = SqliteUnitOfWork(ws.db_path)
    with uow:
        uow.environments.put(
            ServiceEnvironment(environment_id="env_lab", name="lab", created_at=NOW)
        )
        uow.services.put(
            ApiService(
                service_id="svc_aws",
                service_name="payments-api",
                environment_id="env_lab",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        uow.commit()
    return ws, uow


def _source(
    uow: SqliteUnitOfWork,
    *,
    kind: str = "cloudwatch",
    config: dict[str, str] | None = None,
) -> TelemetrySource:
    src = TelemetrySource(
        source_id="src_aws_1",
        kind=kind,
        service_id="svc_aws",
        config=config or {},
        created_at=NOW,
    )
    with uow:
        uow.sources.put(src)
        uow.commit()
    return src


_LAB_QUERIES = json.dumps(
    [
        {
            "namespace": "AWS/ApiGateway",
            "metric_name": "Latency",
            "stat": "Average",
            "unit": "Milliseconds",
            "period_seconds": 300,
        },
        {
            "namespace": "AWS/ApiGateway",
            "metric_name": "5XXError",
            "stat": "Sum",
            "unit": "Count",
            "period_seconds": 300,
        },
    ]
)


class TestCollectAwsSource:
    def _run(
        self,
        tmp_path: Path,
        *,
        kind: str = "cloudwatch",
        script: dict[str, dict[str, list[Any]]] | None = None,
        config: dict[str, str] | None = None,
        budget: AwsCollectBudget | None = None,
        resume: bool = False,
        window_seconds: int = 30 * 60,
    ):
        ws, uow = _workspace(tmp_path)
        src = _source(uow, kind=kind, config=config)
        sim = simulated_clientset(
            script_by_service=script if script is not None else lab013(now=NOW)
        )
        use_case = CollectAwsSource(uow, FixedClock(NOW), SequentialIdFactory())
        report = use_case.execute(
            source=src,
            clients=sim.clients,
            budget=budget or AwsCollectBudget(),
            window_seconds=window_seconds,
            end=NOW,
            resume=resume,
            receipts_dir=ws.receipts_dir,
            sleep=NO_SLEEP,
            jitter=NO_JITTER,
            monotonic=lambda: 0.0,
        )
        return ws, uow, sim, report

    def test_lab013_end_to_end(self, tmp_path: Path) -> None:
        _ws, uow, sim, report = self._run(
            tmp_path,
            config={"queries_json": _LAB_QUERIES, "region": LAB_REGION},
        )
        # two throttles → success; two pages → concatenated; Messages →
        # partial outcome — LAB-013 observable in one receipt
        assert report.throttled_retries == 2
        assert report.pages == 2
        assert report.datapoints == 6
        assert report.outcome == "partial"
        assert report.messages
        assert report.calls > 0
        assert report.estimated_cost_usd > 0
        # throttling calls really hit the simulated transport
        cw_calls = [c for c in sim.call_log if c[0] == "cloudwatch"]
        assert len(cw_calls) == 4  # 2 throttles + 2 pages
        # observations persisted through the real pipeline
        with uow:
            obs = uow.observations.list_for_service("svc_aws")
        assert len(obs) == 6
        # receipt on disk — redacted, masked, no ARN
        assert report.receipt_path
        receipt = Path(report.receipt_path).read_text(encoding="utf-8")
        parsed = json.loads(receipt)
        assert parsed["account_masked"] == "1234****9012"
        assert parsed["account_hash"].startswith("sha256:")
        assert LAB_ACCOUNT not in receipt
        assert "arn:aws:" not in receipt
        assert parsed["throttled_retries"] == 2
        assert parsed["outcome"] == "partial"

    def test_recollect_same_window_dedups(self, tmp_path: Path) -> None:
        config = {"queries_json": _LAB_QUERIES, "region": LAB_REGION}
        _ws, _uow, _s1, r1 = self._run(tmp_path, config=config)
        # second run = new session over the same canned window
        _ws2, uow2, _s2, r2 = self._run(tmp_path, config=config)
        assert r1.observations == r2.observations == 6
        # identical content → every item skipped by batch dedup
        assert r2.accepted == 0
        assert r2.skipped_duplicates == 6
        with uow2:
            assert uow2.observations.count() == 6  # not 12

    def test_checkpoint_written_and_resume_respected(self, tmp_path: Path) -> None:
        config = {"queries_json": _LAB_QUERIES, "region": LAB_REGION}
        _ws, _uow, _s, r1 = self._run(tmp_path, config=config)
        assert json.loads(r1.checkpoint)["last_window_end"] == NOW.isoformat()
        _w2, _u2, _s2, r2 = self._run(tmp_path, config=config, resume=True)
        assert r2.resumed_from == NOW

    def test_credential_config_refused_before_any_aws_call(self, tmp_path: Path) -> None:
        ws, uow = _workspace(tmp_path)
        src = _source(
            uow,
            config={
                "queries_json": _LAB_QUERIES,
                "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
            },
        )
        sim = simulated_clientset(script_by_service=lab013(now=NOW))
        use_case = CollectAwsSource(uow, FixedClock(NOW), SequentialIdFactory())
        with pytest.raises(IntegrationRefused):
            use_case.execute(
                source=src,
                clients=sim.clients,
                budget=AwsCollectBudget(),
                window_seconds=1800,
                end=NOW,
                receipts_dir=ws.receipts_dir,
                sleep=NO_SLEEP,
                jitter=NO_JITTER,
            )
        assert sim.call_log == []  # refused before ANY transport call
        # the fake key must not appear in any written receipt
        for f in ws.receipts_dir.glob("*.json"):
            assert "AKIAIOSFODNN7EXAMPLE" not in f.read_text(encoding="utf-8")

    def test_lookback_budget_is_named_refusal(self, tmp_path: Path) -> None:
        with pytest.raises(IntegrationRefused) as ei:
            self._run(
                tmp_path,
                config={"queries_json": _LAB_QUERIES, "region": LAB_REGION},
                budget=AwsCollectBudget(max_lookback_minutes=10),
                window_seconds=30 * 60,
            )
        assert "lookback" in str(ei.value)

    def test_throttle_forever_bounded_refusal_with_receipt(self, tmp_path: Path) -> None:
        from api_sentinel.integrations.aws.simulated import throttle_forever

        with pytest.raises(AwsThrottled):
            self._run(
                tmp_path,
                script=throttle_forever(now=NOW),
                config={"queries_json": _LAB_QUERIES, "region": LAB_REGION},
                budget=AwsCollectBudget(max_retries=2),
            )
        # refusal receipt still on disk — terminal, named, redacted
        ws = Workspace(root=tmp_path / "ws", scope="project")
        receipts = list(ws.receipts_dir.glob("*.aws.receipt.json"))
        assert receipts, "refusal must still leave a receipt"
        parsed = json.loads(receipts[0].read_text(encoding="utf-8"))
        assert parsed["outcome"] == "refused"
        assert parsed["throttled_retries"] == 3

    def test_no_queries_is_named_refusal(self, tmp_path: Path) -> None:
        with pytest.raises(IntegrationRefused):
            self._run(tmp_path, config={"queries_json": "[]"})

    def test_api_gateway_source_discovers_then_collects(self, tmp_path: Path) -> None:
        _ws, _uow, sim, report = self._run(
            tmp_path,
            kind="api-gateway",
            config={"region": LAB_REGION},
        )
        # discovery ran (rest + http) then GetMetricData
        services = {svc for svc, _op, _kw in sim.call_log}
        assert {"apigateway", "apigatewayv2", "cloudwatch"} <= services
        # 2 endpoints (REST + HTTP) x 6 base metrics each
        assert report.requested_queries == 12
