import json
from pathlib import Path

import pytest

from api_sentinel.contracts.v1 import ENVELOPES, ServiceIdentityV1, parse_envelope
from api_sentinel.domain import ContractError

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "contracts" / "v1"


def test_every_python_envelope_has_a_json_schema() -> None:
    schema_files = {p.stem.replace(".schema", "") for p in SCHEMA_DIR.glob("*.schema.json")}
    assert len(schema_files) == len(ENVELOPES) == 8
    for p in SCHEMA_DIR.glob("*.schema.json"):
        doc = json.loads(p.read_text(encoding="utf-8"))
        assert doc["$id"].endswith(doc["title"])
        assert doc["title"] in {cls.expected_schema for cls in ENVELOPES.values()}


def test_service_identity_round_trip() -> None:
    env = ServiceIdentityV1.model_validate(
        {
            "schema": "api-sentinel/ServiceIdentity/v1",
            "service_id": "svc_1",
            "environment": "prod",
            "service_name": "payments-api",
            "external_refs": {"openapi_operation_id": "createPayment"},
        }
    )
    assert env.service_name == "payments-api"
    assert ServiceIdentityV1.model_validate(json.loads(env.model_dump_json())) == env


def test_unknown_version_rejected_never_defaulted() -> None:
    with pytest.raises(ContractError):
        parse_envelope({"schema": "api-sentinel/ServiceIdentity/v99"})
    with pytest.raises(Exception):  # noqa: B017
        ServiceIdentityV1.model_validate(
            {
                "schema": "api-sentinel/ServiceIdentity/v2",
                "service_id": "x",
                "environment": "p",
                "service_name": "n",
            }
        )


def test_extra_fields_forbidden_in_envelopes() -> None:
    with pytest.raises(Exception):  # noqa: B017
        ServiceIdentityV1.model_validate(
            {
                "schema": "api-sentinel/ServiceIdentity/v1",
                "service_id": "x",
                "environment": "p",
                "service_name": "n",
                "bogus_field": 1,
            }
        )
