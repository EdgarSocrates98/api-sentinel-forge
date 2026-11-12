"""Contract compatibility matrix — `api-sentinel/<Name>/v1` producer/consumer
rules proven mechanically (docs/13 invariant: "an unknown contract version is
never silently accepted").

For every versioned schema under `contracts/v1/`:

1. a valid example validates against the JSON Schema *and* the typed
   pydantic envelope (producer and consumer agree);
2. an unknown `schema` version is rejected explicitly — never defaulted;
3. removing a required field is rejected;
4. additive-optional content is tolerated where the contract allows it:
   optional declared fields parse, and `additionalProperties: true` zones
   (handoff `payload`, source `config`, observation `attributes`) accept
   producer extension keys.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from pydantic_core import PydanticUndefined

from api_sentinel.contracts.v1.models import (
    ENVELOPES,
    AnomalyV1,
    AtlasExportV1,
    DeployManifestV1,
    EvidenceArtifactV1,
    InvestigationHandoffV1,
    RuntimeObservationV1,
    ServiceIdentityV1,
    TelemetrySourceV1,
    parse_envelope,
)
from api_sentinel.domain import ContractError

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "v1"

_VALID: dict[str, tuple[type, dict[str, Any]]] = {
    "anomaly": (
        AnomalyV1,
        {
            "schema": "api-sentinel/Anomaly/v1",
            "anomaly_id": "anm_1",
            "service_id": "svc_1",
            "detected_at": "2026-01-01T12:00:00Z",
            "rule": {"rule_id": "rule_1", "rule_version": "1"},
            "severity": "SEV3",
            "explanation": {"summary": "error ratio over window"},
            "evidence_refs": [],
            "unknowns": [],
        },
    ),
    "atlas_export": (
        AtlasExportV1,
        {
            "schema": "api-sentinel/AtlasExport/v1",
            "exported_at": "2026-01-01T12:00:00Z",
            "services": [{"atlas_id": "a1", "name": "payments-api"}],
            "edges": [
                {
                    "edge_id": "e1",
                    "from": "a1",
                    "to": "a2",
                    "kind": "CALLS_ENDPOINT",
                }
            ],
        },
    ),
    "deploy_manifest": (
        DeployManifestV1,
        {
            "schema": "api-sentinel/DeployManifest/v1",
            "deploys": [{"service": "svc_1", "at": "2026-01-01T12:00:00Z"}],
        },
    ),
    "evidence_artifact": (
        EvidenceArtifactV1,
        {
            "schema": "api-sentinel/EvidenceArtifact/v1",
            "evidence_id": "ev_1",
            "kind": "metric-export",
            "created_at": "2026-01-01T12:00:00Z",
            "hash": "sha256:" + "a" * 64,
            "provenance": {"source_id": "src_1"},
        },
    ),
    "investigation_handoff": (
        InvestigationHandoffV1,
        {
            "schema": "api-sentinel/InvestigationHandoff/v1",
            "handoff_id": "hf_1",
            "from_forge": "api-sentinel-forge",
            "to_forge": "forge-doctor-api",
            "kind": "investigation",
            "payload": {},
            "constraints": {},
        },
    ),
    "runtime_observation": (
        RuntimeObservationV1,
        {
            "schema": "api-sentinel/RuntimeObservation/v1",
            "observation_id": "obs_1",
            "service_id": "svc_1",
            "window": {
                "start": "2026-01-01T11:58:00Z",
                "end": "2026-01-01T11:59:00Z",
            },
            "source_id": "src_1",
            "quality": "COMPLETE",
            "measurement": {"kind": "counter", "unit": "requests", "value": 1},
        },
    ),
    "service_identity": (
        ServiceIdentityV1,
        {
            "schema": "api-sentinel/ServiceIdentity/v1",
            "service_id": "svc_1",
            "environment": "production",
            "service_name": "payments-api",
            "external_refs": {},
        },
    ),
    "telemetry_source": (
        TelemetrySourceV1,
        {
            "schema": "api-sentinel/TelemetrySource/v1",
            "source_id": "src_1",
            "kind": "jsonl-file",
            "service_id": "svc_1",
            "enabled": True,
            "config": {},
        },
    ),
}


def _load_schema(stem: str) -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / f"{stem}.schema.json").read_text("utf-8"))


#: Producer-side enrichments: declared-optional fields with schema-valid
#: values — a consumer that rejects these breaks compatibility.
_OPTIONAL_ENRICHMENTS: dict[str, dict[str, Any]] = {
    "anomaly": {"operation_id": "op_1", "unknowns": ["window_partial"]},
    "atlas_export": {
        "snapshot_ref": "snap_1",
        "exporter": {"id": "code-atlas-forge", "version": "1.0"},
    },
    "deploy_manifest": {
        "exported_at": "2026-01-01T12:00:00Z",
        "exporter": "github-actions",
    },
    "evidence_artifact": {
        "size_bytes": 10,
        "truncated": False,
        "loss_notes": "tail truncated",
        "blob_ref": "sha256:" + "b" * 64,
    },
    "investigation_handoff": {
        "correlation_id": "cor_1",
        "constraints": {"budget_seconds": 30.0, "scopes": ["read"]},
    },
    "runtime_observation": {
        "operation_id": "op_1",
        "release_id": "rel_1",
        "sampling": {"rate": 1.0, "method": "none"},
        "attributes": {"http.route": "/x"},
    },
    "service_identity": {
        "namespace": "payments",
        "display_name": "Payments API",
        "operation": {"protocol": "http", "method": "GET", "route": "/x"},
    },
    "telemetry_source": {"created_at": "2026-01-01T12:00:00Z"},
}


@pytest.mark.parametrize("stem", sorted(_VALID))
def test_matrix_rows_cover_every_schema(stem: str) -> None:
    """The matrix is exhaustive — every v1 schema file has a row."""
    assert (SCHEMAS_DIR / f"{stem}.schema.json").is_file()
    assert stem in _VALID


def test_every_schema_file_covered() -> None:
    on_disk = {p.stem.replace(".schema", "") for p in SCHEMAS_DIR.glob("*.json")}
    assert on_disk == set(_VALID)


@pytest.mark.parametrize("stem,model_payload", sorted(_VALID.items()))
class TestPerContract:
    def test_valid_example_parses(
        self, stem: str, model_payload: tuple[type, dict[str, Any]]
    ) -> None:
        model_cls, payload = model_payload
        Draft202012Validator(_load_schema(stem)).validate(payload)
        parsed = model_cls.model_validate(payload)
        assert parse_envelope(payload).expected_schema == parsed.expected_schema

    def test_unknown_version_rejected(
        self, stem: str, model_payload: tuple[type, dict[str, Any]]
    ) -> None:
        model_cls, payload = model_payload
        forged = {**payload, "schema": f"api-sentinel/{stem.title()}/v99"}
        assert not Draft202012Validator(_load_schema(stem)).is_valid(forged)
        with pytest.raises(ContractError):
            parse_envelope(forged)
        # a wrong-but-known version is also an explicit ContractError
        other = {**payload, "schema": "api-sentinel/RuntimeObservation/v1"}
        if stem == "runtime_observation":
            other["schema"] = "api-sentinel/Anomaly/v1"
        with pytest.raises(ContractError):
            model_cls.model_validate(other)

    def test_required_field_removal_rejected(
        self, stem: str, model_payload: tuple[type, dict[str, Any]]
    ) -> None:
        model_cls, payload = model_payload
        schema = _load_schema(stem)
        validator = Draft202012Validator(schema)
        for field in schema["required"]:
            stripped = copy.deepcopy(payload)
            stripped.pop(field)
            # producer contract: the schema rejects — always
            assert not validator.is_valid(stripped), f"{stem}: -{field}"
            # consumer contract: the typed envelope either rejects outright
            # or tolerates ONLY because the field has a declared default —
            # never a silent wrong-shape acceptance
            try:
                model_cls.model_validate(stripped)
            except ValidationError:
                pass  # typed consumer rejects too — the strict case
            else:
                model_field = model_cls.model_fields.get(field) or next(
                    f for f in model_cls.model_fields.values() if f.alias == field
                )
                has_default = (
                    model_field.default is not PydanticUndefined
                    or model_field.default_factory is not None
                )
                assert has_default, f"{stem}: -{field} accepted with no declared default"

    def test_additive_optional_tolerated(
        self, stem: str, model_payload: tuple[type, dict[str, Any]]
    ) -> None:
        model_cls, payload = model_payload
        schema = _load_schema(stem)
        validator = Draft202012Validator(schema)
        # declared-optional producer fields must not break consumers:
        # every schema-valid enrichment parses in the typed envelope too
        for field, value in _OPTIONAL_ENRICHMENTS.get(stem, {}).items():
            enriched = copy.deepcopy(payload)
            enriched[field] = value
            assert validator.is_valid(enriched), f"{stem}: +{field} schema"
            model_cls.model_validate(enriched)
        # top-level extension keys are tolerated ONLY where the schema says so
        forged = {**payload, "x_producer_extension": 1}
        if schema.get("additionalProperties") is True:
            assert validator.is_valid(forged)
        else:
            assert not validator.is_valid(forged)
            with pytest.raises(ValidationError):
                model_cls.model_validate(forged)


class TestExtensionZones:
    """`additionalProperties: true` zones accept producer extensions."""

    def test_handoff_payload_extension(self) -> None:
        payload = copy.deepcopy(_VALID["investigation_handoff"][1])
        payload["payload"] = {"new_kind_field": "x", "nested": {"a": 1}}
        Draft202012Validator(_load_schema("investigation_handoff")).validate(payload)
        InvestigationHandoffV1.model_validate(payload)

    def test_source_config_extension(self) -> None:
        payload = copy.deepcopy(_VALID["telemetry_source"][1])
        payload["config"] = {"region": "us-east-1", "future_knob": "on"}
        Draft202012Validator(_load_schema("telemetry_source")).validate(payload)
        TelemetrySourceV1.model_validate(payload)

    def test_observation_attributes_extension(self) -> None:
        payload = copy.deepcopy(_VALID["runtime_observation"][1])
        payload["attributes"] = {"custom.dimension": "v", "n": 3}
        Draft202012Validator(_load_schema("runtime_observation")).validate(payload)
        RuntimeObservationV1.model_validate(payload)

    def test_envelope_catalog_matches_schemas(self) -> None:
        """ENVELOPES covers every schema file — no unmapped contract."""
        assert len(ENVELOPES) == len(_VALID)


class TestCycleTenNegatives:
    """The Wave-1 negative battery — every refusal is a typed error,
    never a default, a silent repair or a silent wrong-shape accept."""

    def test_duplicate_ids_rejected(self) -> None:
        """Atlas export: duplicate service or edge ids are a contract
        violation — a merge would fabricate structure."""
        payload = copy.deepcopy(_VALID["atlas_export"][1])
        payload["services"].append({"atlas_id": "a1", "name": "dup"})
        with pytest.raises(ContractError):
            AtlasExportV1.model_validate(payload)
        payload = copy.deepcopy(_VALID["atlas_export"][1])
        payload["edges"].append(copy.deepcopy(payload["edges"][0]))
        with pytest.raises(ContractError):
            AtlasExportV1.model_validate(payload)

    def test_invalid_timestamp_rejected(self) -> None:
        payload = copy.deepcopy(_VALID["runtime_observation"][1])
        payload["window"]["start"] = "not-a-date"
        with pytest.raises(ValidationError):
            RuntimeObservationV1.model_validate(payload)

    def test_invalid_dimension_type_rejected(self) -> None:
        """A non-string measurement unit is a shape violation — typed
        consumer refuses, never coerces."""
        payload = copy.deepcopy(_VALID["runtime_observation"][1])
        payload["measurement"]["unit"] = 5
        with pytest.raises(ValidationError):
            RuntimeObservationV1.model_validate(payload)

    def test_hash_pattern_enforced(self) -> None:
        """EvidenceArtifact.hash must be `sha256:<64-lower-hex>` — the
        wire schema and the typed envelope agree."""
        schema = _load_schema("evidence_artifact")
        validator = Draft202012Validator(schema)
        for bad in ("deadbeef", "md5:" + "a" * 32, "sha256:" + "A" * 64, "sha256:" + "a" * 63):
            payload = copy.deepcopy(_VALID["evidence_artifact"][1])
            payload["hash"] = bad
            assert not validator.is_valid(payload), bad
            with pytest.raises(ValidationError):
                EvidenceArtifactV1.model_validate(payload)

    def test_truncated_payload_rejected(self) -> None:
        """A truncated envelope is a validation failure at both layers —
        the JSON never parses as the real contract."""
        raw = json.dumps(_VALID["investigation_handoff"][1])
        truncated = raw[: len(raw) // 2]
        with pytest.raises(json.JSONDecodeError):
            json.loads(truncated)
        # truncated object — required fields without defaults gone —
        # both layers refuse
        payload = copy.deepcopy(_VALID["investigation_handoff"][1])
        del payload["handoff_id"], payload["kind"]
        assert not Draft202012Validator(_load_schema("investigation_handoff")).is_valid(payload)
        with pytest.raises(ValidationError):
            InvestigationHandoffV1.model_validate(payload)

    def test_broken_envelope_reference_rejected(self) -> None:
        """A payload naming no known `schema` is a `ContractError` —
        an unknown contract is never defaulted."""
        with pytest.raises(ContractError):
            parse_envelope({"handoff_id": "hf_1"})
        with pytest.raises(ContractError):
            parse_envelope({"schema": "api-sentinel/Handoff/v0"})

    def test_inconsistent_correlation_rejected(self) -> None:
        """A Doctor bundle echoing a different handoff id is refused —
        response correlation is verified, never assumed."""
        from api_sentinel.integrations.doctor.translate import (
            ingest_doctor_bundle,
        )

        bundle = {"handoff_version": 2, "handoff_id": "hnd_other", "findings": []}
        with pytest.raises(ContractError, match="correlation"):
            ingest_doctor_bundle(bundle, expected_handoff_id="hnd_mine")
        # matching correlation passes
        ok = ingest_doctor_bundle(
            {**bundle, "handoff_id": "hnd_mine"},
            expected_handoff_id="hnd_mine",
        )
        assert ok.handoff_id == "hnd_mine"

    def test_incomplete_provider_result_refused(self) -> None:
        """A forge/v1 response missing status/kind/protocol is a typed
        refusal — the adapter never guesses a result."""
        from api_sentinel.integrations.errors import (
            IntegrationRefused,
            IntegrationUnavailable,
        )
        from api_sentinel.integrations.the_forge.handoff import (
            response_to_result,
        )

        with pytest.raises(IntegrationUnavailable):
            response_to_result("not-a-dict")  # type: ignore[arg-type]
        with pytest.raises(IntegrationRefused):
            response_to_result({})  # missing protocol
        with pytest.raises(IntegrationRefused):
            response_to_result(
                {"protocol": "forge/v1", "kind": "Response"} | {"status": "half-done"}
            )
