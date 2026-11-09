"""AWS client resolution + the mechanical read-only boundary.

Two responsibilities:

1. Session resolution — profile → role → default credential chain. The
   adapter *never* embeds, stores or logs credentials; a role is assumed
   through STS and the temporary credentials stay inside the boto3
   session object in memory.

2. `ReadOnlyClient` — the security boundary (ADR-013). It exposes only
   allowlisted read operations (`get_*`, `list_*`, `describe_*`,
   `query`); any other attribute raises `IntegrationUnavailable` before
   the underlying client is touched. `prove_read_only()` demonstrates
   this mechanically — it is what flips the
   `api.aws-readonly-observability` capability from DECLARED to
   AVAILABLE.
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from typing import Any

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.integrations.aws.errors import (
    aws_refused,
    aws_unavailable,
)
from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)

# The allowlist IS the security boundary: read operations only. Anything
# else — including prefix-matching escape hatches — is refused before
# transport.
_ALLOWED_PREFIXES = ("describe_", "get_", "list_")
_ALLOWED_OPERATIONS = frozenset({"query"})
# `get_*` attributes that must NOT pass the prefix check: paginators and
# waiters can wrap arbitrary operations (including mutating ones), so
# they stay unreachable through the proxy.
_BLOCKED_ATTRIBUTES = frozenset(
    {"get_paginator", "get_waiter", "get_paginators", "get_waiter_names"}
)

_ROLE_ARN_RE = re.compile(r"^arn:aws[a-zA-Z-]*:iam::\d{12}:role/[\w+=,.@/-]+$")
_REGION_RE = re.compile(r"^[a-z]{2}(-gov|-iso[a-z]?)?-[a-z]+-\d$")


def is_allowed_operation(name: str) -> bool:
    """True iff `name` is a read operation on the allowlist.

    Pure predicate, kept separate so tests and the proof exercise the
    exact rule the proxy enforces — the proof is mechanical, not a
    code-read promise.
    """
    if name in _BLOCKED_ATTRIBUTES:
        return False
    if name in _ALLOWED_OPERATIONS:
        return True
    return name.startswith(_ALLOWED_PREFIXES)


def boto3_available() -> bool:
    """True iff boto3 resolves — never import-for-side-effect."""
    return importlib.util.find_spec("boto3") is not None


class ReadOnlyClient:
    """Allowlist proxy over an AWS service client — read-only by construction.

    `__getattr__` runs only for names the class does not define; every
    non-allowlisted attribute raises `IntegrationUnavailable` *before*
    the wrapped client is consulted, so a mutating call can never reach
    the transport through this object. Pagination is done manually by
    callers via `NextToken`/`position` loops — `get_paginator` is
    deliberately unreachable.
    """

    def __init__(self, client: Any, *, service: str) -> None:
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_service", service)

    @property
    def service(self) -> str:
        service: str = object.__getattribute__(self, "_service")
        return service

    def __setattr__(self, name: str, value: Any) -> None:
        raise aws_unavailable(
            f"read-only client is immutable: cannot set {name!r}",
            details={"service": self.service, "attribute": name},
        )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if not is_allowed_operation(name):
            raise aws_unavailable(
                f"read-only allowlist refused {name!r} pre-transport — "
                "mutating AWS calls are unreachable by construction (ADR-013)",
                details={"service": self.service, "attribute": name},
            )
        client = object.__getattribute__(self, "_client")
        try:
            attr = getattr(client, name)
        except AttributeError as exc:
            raise aws_unavailable(
                f"{self.service} client exposes no operation {name!r}",
                details={"service": self.service, "attribute": name},
            ) from exc
        if not callable(attr):
            raise aws_unavailable(
                f"attribute {name!r} on {self.service} client is not callable",
                details={"service": self.service, "attribute": name},
            )
        return attr


class _ProbeClient:
    """Records every attribute read — the proof's wiretap on the transport."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        def _record(*args: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(name)
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

        return _record


def prove_read_only() -> tuple[bool, str]:
    """Execute the mechanical read-only proof (spec: verifiable, not aspirational).

    Steps:
    1. Mutating-style names (`terminate_instances`, `put_metric_data`,
       `delete_alarms`, `create_api_key`) must raise a named refusal
       *before* the wrapped transport is touched.
    2. Paginator/waiter escape hatches are refused too.
    3. An allowlisted call does reach the transport — the proxy is not
       just a dead object.
    Returns (passed, human detail). A failure keeps the capability
    DECLARED with the reason — never a silent upgrade.
    """
    probe = _ProbeClient()
    ro = ReadOnlyClient(probe, service="cloudwatch")
    for name in (
        "terminate_instances",
        "put_metric_data",
        "delete_alarms",
        "create_api_key",
        "get_paginator",
        "meta",
    ):
        try:
            getattr(ro, name)
        except IntegrationUnavailable:
            continue
        except IntegrationRefused:
            continue
        return False, f"attribute {name!r} was not refused by the allowlist"
    if probe.calls:
        return False, f"refused attribute reached the transport: {probe.calls}"
    try:
        ro.describe_alarms()
    except IntegrationUnavailable as exc:
        return False, f"allowlisted call refused: {exc.reason}"
    if probe.calls != ["describe_alarms"]:
        return False, f"allowlisted call did not reach transport: {probe.calls}"
    return True, (
        "mechanical proof: 6 mutating/escape attributes refused pre-transport "
        "with zero calls issued; describe_alarms reached the transport"
    )


class AwsSessionConfig(FrozenModel):
    """Credential/scope config — credentials themselves never appear here."""

    profile: str | None = None
    region: str | None = None
    role_arn: str | None = None
    expected_account_id: str | None = None

    @classmethod
    def from_source_config(cls, config: dict[str, str]) -> AwsSessionConfig:
        """Build session config from a TelemetrySource config dict.

        Secret-shaped keys are refused outright — the environment holds
        credentials (profiles/SSO/workload identity), Sentinel config
        holds scope only.
        """
        secret_keys = sorted(
            k
            for k in config
            if k.lower()
            in {
                "aws_access_key_id",
                "aws_secret_access_key",
                "aws_session_token",
                "access_key",
                "secret_key",
                "secret",
                "token",
                "password",
            }
        )
        if secret_keys:
            raise aws_refused(
                "source config carries credential-shaped keys — credentials "
                "are held by the environment, never by Sentinel config",
                details={"refused_keys": ",".join(secret_keys)},
            )
        return cls(
            profile=config.get("profile") or None,
            region=config.get("region") or None,
            role_arn=config.get("role_arn") or None,
            expected_account_id=config.get("account_id") or None,
        )


def _resolve_region(config: AwsSessionConfig, session_region: str | None) -> str:
    region = config.region or session_region
    if not region:
        raise aws_unavailable(
            "no region resolved — an explicit account/region scope is "
            "required (set region in the source config or AWS config)",
        )
    if not _REGION_RE.match(region):
        raise aws_refused(
            f"unparseable region scope: {region!r}",
            details={"region": region},
        )
    return region


def resolve_session(config: AwsSessionConfig) -> tuple[Any, str]:
    """Resolve a boto3 session: profile → role → default chain.

    Temporary credentials from `sts:AssumeRole` stay inside the returned
    session object — they are never written to config, receipts or logs.
    """
    if not boto3_available():
        raise aws_unavailable(
            "boto3 is not installed — the 'aws' extra is required "
            "(pip install api-sentinel-forge[aws])",
            details={"install": "api-sentinel-forge[aws]"},
        )
    # boto3/botocore ship no stubs; resolved via mypy override in
    # pyproject.toml ([[tool.mypy.overrides]]) — portable across
    # platforms where untyped-vs-not-found differs
    import boto3
    import botocore.exceptions

    try:
        if config.profile:
            session = boto3.Session(
                profile_name=config.profile, region_name=config.region
            )
        else:
            session = boto3.Session(region_name=config.region)
    except botocore.exceptions.BotoCoreError as exc:
        raise aws_unavailable(
            f"could not resolve AWS session: {type(exc).__name__}: {exc}",
            details={"profile": "set" if config.profile else "unset"},
        ) from exc
    except Exception as exc:
        raise aws_unavailable(
            f"could not resolve AWS session: {type(exc).__name__}: {exc}",
        ) from exc

    if config.role_arn:
        if not _ROLE_ARN_RE.match(config.role_arn):
            raise aws_refused(
                f"unparseable role ARN: {config.role_arn!r}",
                details={"role_arn": "malformed"},
            )
        try:
            creds = session.client("sts").assume_role(
                RoleArn=config.role_arn,
                RoleSessionName="api-sentinel-readonly",
            )["Credentials"]
        except Exception as exc:
            raise aws_unavailable(
                f"assume_role failed: {type(exc).__name__}: {exc}",
            ) from exc
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=config.region,
        )

    return session, _resolve_region(config, session.region_name)


@dataclass(frozen=True)
class AwsClientSet:
    """The four read-only clients a collection cycle uses.

    `region` carries the resolved scope so the STS preflight can verify
    it against the configured expectation.
    """

    sts: ReadOnlyClient
    cloudwatch: ReadOnlyClient
    apigateway: ReadOnlyClient
    apigatewayv2: ReadOnlyClient
    region: str


def real_clientset(config: AwsSessionConfig) -> AwsClientSet:
    """Build the ReadOnlyClient-wrapped service set over a real session."""
    session, region = resolve_session(config)
    return AwsClientSet(
        sts=ReadOnlyClient(session.client("sts", region_name=region), service="sts"),
        cloudwatch=ReadOnlyClient(
            session.client("cloudwatch", region_name=region), service="cloudwatch"
        ),
        apigateway=ReadOnlyClient(
            session.client("apigateway", region_name=region), service="apigateway"
        ),
        apigatewayv2=ReadOnlyClient(
            session.client("apigatewayv2", region_name=region), service="apigatewayv2"
        ),
        region=region,
    )
