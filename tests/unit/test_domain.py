from datetime import UTC, datetime

import pytest

from api_sentinel.domain import (
    ApiOperation,
    FixedClock,
    OperationIdentity,
    QualityState,
    SentinelError,
    SequentialIdFactory,
    SignalType,
    ValidationError,
    normalize_route,
)


def test_normalize_route_collapses_ids() -> None:
    assert normalize_route("/payments/42/items/7") == "/payments/{param}/items/{param}"
    assert normalize_route("/payments/{payment_id}") == "/payments/{payment_id}"
    assert normalize_route("/a/" + "f" * 40) == "/a/{param}"


def test_quality_and_signal_enums_complete() -> None:
    assert {s.value for s in QualityState} == {
        "COMPLETE", "PARTIAL", "SAMPLED", "STALE", "INVALID", "UNKNOWN"
    }
    assert len(SignalType) == 8


def test_ids_carry_prefix_and_are_deterministic() -> None:
    ids = SequentialIdFactory()
    assert ids.new("svc") == "svc_0001"
    assert ids.new("svc") == "svc_0002"


def test_fixed_clock_is_deterministic() -> None:
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    assert FixedClock(instant).now() == instant


def test_error_has_stable_code_and_details() -> None:
    err = ValidationError("bad", details={"field": "x"})
    assert err.code == "sentinel.validation"
    assert err.to_dict()["details"] == {"field": "x"}
    assert isinstance(err, SentinelError)


def test_operation_make_normalizes_route() -> None:
    op = ApiOperation.make(
        operation_id="op_1",
        service_id="svc_1",
        identity=OperationIdentity(protocol="http", method="GET", route="/users/99"),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert op.normalized_route == "/users/{param}"


def test_domain_models_are_frozen() -> None:
    op = OperationIdentity(protocol="http")
    with pytest.raises(Exception):  # noqa: B017 — pydantic frozen violation
        op.method = "X"  # type: ignore[misc]
