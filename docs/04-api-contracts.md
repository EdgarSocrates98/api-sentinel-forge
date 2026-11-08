# 04 — API Contracts

Public envelopes are versioned JSON Schemas under `contracts/v1/`;
`src/api_sentinel/contracts/v1/` carries the matching typed models.
Internal code uses typed models; the wire uses schemas.

## Envelopes (v1)

| Schema | Purpose |
| --- | --- |
| `api-sentinel/ServiceIdentity/v1` | canonical service identity |
| `api-sentinel/RuntimeObservation/v1` | normalized measurement over a window |
| `api-sentinel/Anomaly/v1` | detected deviation + evidence refs |
| `api-sentinel/InvestigationHandoff/v1` | governed delegation envelope |
| `api-sentinel/TelemetrySource/v1` | authorized source declaration |
| `api-sentinel/EvidenceArtifact/v1` | verifiable evidence reference |

Illustrative payloads live inline in the blueprint; the normative shape is
the JSON Schema files. A histogram HTTP instrument must never be reduced
to a synthetic request counter.

## Local HTTP surface (minimum)

| Method | Endpoint | Role |
| --- | --- | --- |
| GET | `/v1/health` | Sentinel health |
| GET | `/v1/capabilities` | real capabilities |
| GET | `/v1/services` | service list |
| GET | `/v1/services/{id}` | service detail |
| GET | `/v1/services/{id}/operations` | identified operations |
| GET | `/v1/services/{id}/metrics` | aggregated series |
| GET | `/v1/services/{id}/slos` | reliability indicators |
| POST | `/v1/ingestion/jobs` | start authorized import |
| GET | `/v1/ingestion/jobs/{id}` | import state |
| GET | `/v1/anomalies` | anomalies |
| GET | `/v1/incidents` | incidents |
| GET | `/v1/incidents/{id}` | investigation detail |
| GET | `/v1/incidents/{id}/timeline` | related events |
| POST | `/v1/incidents/{id}/investigations` | start controlled investigation |
| GET | `/v1/investigations/{id}` | result |
| GET | `/v1/evidence/{id}` | authorized metadata |
| GET | `/v1/integrations/status` | specialist states |

No generic `/execute` endpoints. Imports/analyses/investigations carry
scope control, concurrency limits, idempotency, audit records and async
responses where appropriate.

## Evolution rules

- Optional additive fields may keep the version when compatible.
- Meaning changes require a new version.
- Units are always explicit.
- Consumers reject incompatible versions explainably.
- Unknowns never convert silently to defaults.
- Evidence has size bounds.
- References resolve under authorization.
- Responses preserve original errors and limitations.
