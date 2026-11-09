"""API Gateway discovery — REST (apigateway) + HTTP (apigatewayv2).

Metric detail and dimensions differ between REST and HTTP APIs
(§16.3): REST APIs index `AWS/ApiGateway` metrics by `ApiName`+`Stage`
(+`Method`,`Resource` when detailed); HTTP APIs by `ApiId`+`Stage`
(+`Route`,`Method`). Per-route detailed metrics are opt-in and
cost-bearing — flagged `detailed_metrics` in source config, bounded by
`max_detailed_routes_per_api`.

Gateway-vs-application failures stay distinct (docs/05): `4XXError`/
`5XXError` are gateway-side outcomes; `IntegrationError`/
`IntegrationLatency` describe the backend leg. Every query carries an
`aws.failure_scope` attribute so the two are never conflated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import Any

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.integrations.aws.budgets import (
    AwsCollectBudget,
    BudgetMeter,
    JitteredBackoff,
    call_with_backoff,
)
from api_sentinel.integrations.aws.cloudwatch import MetricQuery

APIGW_NAMESPACE = "AWS/ApiGateway"

# metric_name → (stat, failure_scope). Latency/IntegrationLatency use
# Average (ms); counts use Sum. HTTP APIs spell errors 4xx/5xx.
REST_API_METRICS: dict[str, tuple[str, str]] = {
    "Count": ("Sum", "total"),
    "4XXError": ("Sum", "gateway"),
    "5XXError": ("Sum", "gateway"),
    "IntegrationError": ("Sum", "integration"),
    "Latency": ("Average", "total"),
    "IntegrationLatency": ("Average", "integration"),
}
HTTP_API_METRICS: dict[str, tuple[str, str]] = {
    "Count": ("Sum", "total"),
    "4xx": ("Sum", "gateway"),
    "5xx": ("Sum", "gateway"),
    "IntegrationError": ("Sum", "integration"),
    "Latency": ("Average", "total"),
    "IntegrationLatency": ("Average", "integration"),
}

_LATENCY_UNIT = "Milliseconds"
_COUNT_UNIT = "Count"


class GatewayRoute(FrozenModel):
    """One route surface for detailed (per-route) metrics — opt-in only."""

    method: str
    resource: str  # REST: /pets/{id} ; HTTP: route path


class ApiGatewayEndpoint(FrozenModel):
    """One API + one stage — the dimension set for metric queries."""

    api_id: str
    api_name: str
    api_type: str  # REST | HTTP
    stage: str


@dataclass
class GatewayInventory:
    """Discovery result: endpoints + named unknowns, never silent gaps."""

    endpoints: list[ApiGatewayEndpoint] = field(default_factory=list)
    rest_routes: dict[str, list[GatewayRoute]] = field(default_factory=dict)
    http_routes: dict[str, list[GatewayRoute]] = field(default_factory=dict)
    unknowns: list[str] = field(default_factory=list)
    truncated: bool = False


def _rest_apis_pages(
    client: Any,
    *,
    meter: BudgetMeter,
    backoff: JitteredBackoff,
    budget: AwsCollectBudget,
    inventory: GatewayInventory,
) -> list[dict[str, Any]]:
    """`get_rest_apis` paginates via `position` — a NextToken-shaped loop."""
    apis: list[dict[str, Any]] = []
    position: str | None = None
    pages = 0
    while True:
        kwargs: dict[str, Any] = {"limit": 500}
        if position:
            kwargs["position"] = position
        resp = call_with_backoff(
            partial(client.get_rest_apis, **kwargs),
            meter=meter,
            backoff=backoff,
            max_retries=budget.max_retries,
            operation="apigateway:GetRestApis",
        )
        pages += 1
        apis.extend(resp.get("items") or [])
        position = resp.get("position")
        if not position:
            break
        if pages >= budget.max_pages_per_call:
            inventory.truncated = True
            inventory.unknowns.append(
                "page_cap:get_rest_apis chain truncated"
            )
            break
    return apis


def _rest_stages(
    client: Any,
    api_id: str,
    *,
    meter: BudgetMeter,
    backoff: JitteredBackoff,
    budget: AwsCollectBudget,
) -> list[str]:
    resp = call_with_backoff(
        lambda: client.get_stages(restApiId=api_id),
        meter=meter,
        backoff=backoff,
        max_retries=budget.max_retries,
        operation="apigateway:GetStages",
    )
    return sorted(
        str(s["stageName"]) for s in resp.get("item") or [] if s.get("stageName")
    )


def _rest_resources(
    client: Any,
    api_id: str,
    *,
    meter: BudgetMeter,
    backoff: JitteredBackoff,
    budget: AwsCollectBudget,
    inventory: GatewayInventory,
) -> list[GatewayRoute]:
    """REST resources → (method, path) pairs; bounded, opt-in only."""
    routes: list[GatewayRoute] = []
    position: str | None = None
    pages = 0
    while True:
        kwargs: dict[str, Any] = {"restApiId": api_id, "limit": 500}
        if position:
            kwargs["position"] = position
        resp = call_with_backoff(
            partial(client.get_resources, **kwargs),
            meter=meter,
            backoff=backoff,
            max_retries=budget.max_retries,
            operation="apigateway:GetResources",
        )
        pages += 1
        for item in resp.get("items") or []:
            path = str(item.get("path") or "/")
            for method in sorted((item.get("resourceMethods") or {}).keys()):
                routes.append(GatewayRoute(method=method, resource=path))
        position = resp.get("position")
        if not position:
            break
        if pages >= budget.max_pages_per_call:
            inventory.truncated = True
            inventory.unknowns.append(
                f"page_cap:get_resources for {api_id} truncated"
            )
            break
    if len(routes) > budget.max_detailed_routes_per_api:
        inventory.unknowns.append(
            f"route_cap:{api_id} has {len(routes)} routes; first "
            f"{budget.max_detailed_routes_per_api} collected "
            "(detailed metrics are cost-bearing)"
        )
        routes = routes[: budget.max_detailed_routes_per_api]
    return routes


def _http_apis_pages(
    client: Any,
    *,
    meter: BudgetMeter,
    backoff: JitteredBackoff,
    budget: AwsCollectBudget,
    inventory: GatewayInventory,
) -> list[dict[str, Any]]:
    """apigatewayv2 `get_apis` paginates via `NextToken`."""
    apis: list[dict[str, Any]] = []
    token: str | None = None
    pages = 0
    while True:
        kwargs: dict[str, Any] = {"MaxResults": "100"}
        if token:
            kwargs["NextToken"] = token
        resp = call_with_backoff(
            partial(client.get_apis, **kwargs),
            meter=meter,
            backoff=backoff,
            max_retries=budget.max_retries,
            operation="apigatewayv2:GetApis",
        )
        pages += 1
        apis.extend(resp.get("Items") or [])
        token = resp.get("NextToken")
        if not token:
            break
        if pages >= budget.max_pages_per_call:
            inventory.truncated = True
            inventory.unknowns.append("page_cap:GetApis chain truncated")
            break
    return apis


def _http_stages(
    client: Any,
    api_id: str,
    *,
    meter: BudgetMeter,
    backoff: JitteredBackoff,
    budget: AwsCollectBudget,
) -> list[str]:
    resp = call_with_backoff(
        lambda: client.get_stages(ApiId=api_id),
        meter=meter,
        backoff=backoff,
        max_retries=budget.max_retries,
        operation="apigatewayv2:GetStages",
    )
    return sorted(
        str(s["StageName"]) for s in resp.get("Items") or [] if s.get("StageName")
    )


def _http_routes(
    client: Any,
    api_id: str,
    *,
    meter: BudgetMeter,
    backoff: JitteredBackoff,
    budget: AwsCollectBudget,
    inventory: GatewayInventory,
) -> list[GatewayRoute]:
    """HTTP API routes → (method, path) from RouteKey; bounded, opt-in."""
    routes: list[GatewayRoute] = []
    token: str | None = None
    pages = 0
    while True:
        kwargs: dict[str, Any] = {"ApiId": api_id, "MaxResults": "100"}
        if token:
            kwargs["NextToken"] = token
        resp = call_with_backoff(
            partial(client.get_routes, **kwargs),
            meter=meter,
            backoff=backoff,
            max_retries=budget.max_retries,
            operation="apigatewayv2:GetRoutes",
        )
        pages += 1
        for item in resp.get("Items") or []:
            key = str(item.get("RouteKey") or "")
            method, _, path = key.partition(" ")
            if path:
                routes.append(GatewayRoute(method=method or "ANY", resource=path))
        token = resp.get("NextToken")
        if not token:
            break
        if pages >= budget.max_pages_per_call:
            inventory.truncated = True
            inventory.unknowns.append(
                f"page_cap:get_routes for {api_id} truncated"
            )
            break
    if len(routes) > budget.max_detailed_routes_per_api:
        inventory.unknowns.append(
            f"route_cap:{api_id} has {len(routes)} routes; first "
            f"{budget.max_detailed_routes_per_api} collected "
            "(detailed metrics are cost-bearing)"
        )
        routes = routes[: budget.max_detailed_routes_per_api]
    return routes


def discover_gateways(
    rest_client: Any,
    http_client: Any,
    *,
    meter: BudgetMeter,
    backoff: JitteredBackoff,
    budget: AwsCollectBudget,
    detailed_metrics: bool = False,
) -> GatewayInventory:
    """Discover REST + HTTP APIs and their stages (all paged, all budgeted).

    `detailed_metrics=True` additionally resolves routes per API — the
    cost-bearing opt-in path (§16.3 note on per-route pricing).
    """
    inventory = GatewayInventory()
    for api in _rest_apis_pages(
        rest_client, meter=meter, backoff=backoff, budget=budget,
        inventory=inventory,
    ):
        api_id = str(api.get("id") or "")
        name = str(api.get("name") or api_id)
        if not api_id:
            inventory.unknowns.append("rest_api_without_id")
            continue
        stages = _rest_stages(
            rest_client, api_id, meter=meter, backoff=backoff, budget=budget
        )
        for stage in stages:
            inventory.endpoints.append(
                ApiGatewayEndpoint(
                    api_id=api_id, api_name=name, api_type="REST", stage=stage
                )
            )
        if detailed_metrics:
            inventory.rest_routes[api_id] = _rest_resources(
                rest_client, api_id, meter=meter, backoff=backoff,
                budget=budget, inventory=inventory,
            )
    for api in _http_apis_pages(
        http_client, meter=meter, backoff=backoff, budget=budget,
        inventory=inventory,
    ):
        api_id = str(api.get("ApiId") or "")
        name = str(api.get("Name") or api_id)
        protocol = str(api.get("ProtocolType") or "HTTP")
        if not api_id:
            inventory.unknowns.append("http_api_without_id")
            continue
        if protocol.upper() == "WEBSOCKET":
            # WS APIs carry different metrics/dims — out of scope, named.
            inventory.unknowns.append(f"websocket_api_skipped:{api_id}")
            continue
        stages = _http_stages(
            http_client, api_id, meter=meter, backoff=backoff, budget=budget
        )
        for stage in stages:
            inventory.endpoints.append(
                ApiGatewayEndpoint(
                    api_id=api_id, api_name=name, api_type="HTTP", stage=stage
                )
            )
        if detailed_metrics:
            inventory.http_routes[api_id] = _http_routes(
                http_client, api_id, meter=meter, backoff=backoff,
                budget=budget, inventory=inventory,
            )
    return inventory


def apigateway_metric_queries(
    inventory: GatewayInventory, *, period_seconds: int = 300
) -> list[MetricQuery]:
    """Map every discovered API+stage → its CloudWatch metric queries.

    Dimensions follow the API type (REST: ApiName/Stage, HTTP:
    ApiId/Stage); detailed routes add the route-level dimensions only
    for APIs whose routes were resolved (the opt-in path).
    """
    queries: list[MetricQuery] = []
    for ep in inventory.endpoints:
        table = REST_API_METRICS if ep.api_type == "REST" else HTTP_API_METRICS
        base_dims = (
            {"ApiName": ep.api_name, "Stage": ep.stage}
            if ep.api_type == "REST"
            else {"ApiId": ep.api_id, "Stage": ep.stage}
        )
        base_attrs = {
            "aws.apigateway.api_id": ep.api_id,
            "aws.apigateway.api_name": ep.api_name,
            "aws.apigateway.api_type": ep.api_type,
            "aws.apigateway.stage": ep.stage,
        }
        route_sets: list[tuple[dict[str, str], dict[str, str]]] = [
            (base_dims, {})
        ]
        routes = (
            inventory.rest_routes.get(ep.api_id)
            if ep.api_type == "REST"
            else inventory.http_routes.get(ep.api_id)
        ) or []
        for route in routes:
            if ep.api_type == "REST":
                route_dims = {
                    **base_dims,
                    "Method": route.method,
                    "Resource": route.resource,
                }
            else:
                route_dims = {
                    **base_dims,
                    "Method": route.method,
                    "Route": route.resource,
                }
            route_sets.append(
                (
                    route_dims,
                    {
                        "aws.apigateway.method": route.method,
                        "aws.apigateway.route": route.resource,
                        "aws.apigateway.detail": "per-route",
                    },
                )
            )
        for dims, extra_attrs in route_sets:
            for metric_name, (stat, scope) in sorted(table.items()):
                unit = (
                    _LATENCY_UNIT if "Latency" in metric_name else _COUNT_UNIT
                )
                queries.append(
                    MetricQuery(
                        namespace=APIGW_NAMESPACE,
                        metric_name=metric_name,
                        dimensions=dims,
                        stat=stat,
                        period_seconds=period_seconds,
                        unit=unit,
                        attributes={
                            **base_attrs,
                            "aws.failure_scope": scope,
                            **extra_attrs,
                        },
                    )
                )
    return queries
