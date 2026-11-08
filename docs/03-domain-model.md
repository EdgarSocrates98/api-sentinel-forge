# 03 — Domain Model (DDD)

## Entities

| Entity | Responsibility | Identity |
| --- | --- | --- |
| `ApiService` | observed logical service | `service_id` |
| `ServiceEnvironment` | logical instance per environment | `environment_id` |
| `ApiOperation` | method/route or protocol operation | `operation_id` |
| `ServiceRelease` | version/deployment | `release_id` |
| `TelemetrySource` | origin of observations | `source_id` |
| `ObservationBatch` | received batch | `batch_id` |
| `MetricSeries` | series pinned by dimensions | `series_id` |
| `MetricPoint` | temporal value/distribution | composite key |
| `SloDefinition` | reliability objective | `slo_id` + revision |
| `SloAssessment` | evaluation over a window | `assessment_id` |
| `DetectionRule` | versioned rule | `rule_id` + revision |
| `Anomaly` | detected deviation | `anomaly_id` |
| `Incident` | operational investigation | `incident_id` |
| `EvidenceArtifact` | verifiable reference | `evidence_id` |
| `ChangeEvent` | deploy/config/change | `change_id` |
| `DependencyObservation` | observed inter-service edge | `edge_id` |
| `Investigation` | investigation execution | `investigation_id` |
| `ExternalHandoff` | delegation to a specialist | `handoff_id` |

## Canonical identity — critical decision

The friendly API name is never the unique identity.

```json
{
  "schema": "api-sentinel/ServiceIdentity/v1",
  "service_id": "svc_01",
  "environment": "production",
  "namespace": "payments",
  "service_name": "payments-api",
  "operation": { "protocol": "http", "method": "POST", "route": "/payments/{payment_id}" },
  "external_refs": {
    "cloud_resource": null,
    "code_atlas_identity": null,
    "openapi_operation_id": "createPayment"
  }
}
```

Three identity kinds, each with provenance:

- **Logical**: service and operation as stable concepts.
- **Deployment**: region, cluster, account, stage, version, instance.
- **Observation**: how a telemetry source labels the service.

Two `payments-api` in different accounts are different services. The
deployment version does not compose logical identity but stays available
for segmentation and release comparison (ADR-012).

## Quality and provenance

Every observation answers: who produced it; when it occurred; when
received; the interval represented; the unit; collection completeness;
sampling; aggregation; applied transform; gaps/inconsistencies; adapter
version.

Data carries a **quality state** orthogonal to the operational conclusion:
`COMPLETE | PARTIAL | SAMPLED | STALE | INVALID | UNKNOWN`. A healthy API
may sit behind a partial source; a sound source may observe a down API.

## Invariants

- Reprocessing a batch never duplicates points.
- Unsampled data is never labeled sampled, nor the inverse.
- A missing source never yields fabricated health.
- Percentiles are never averaged; distributions are aggregated first.
- Counter resets never yield negative rates.
- Truncated evidence declares the loss.
- Unknown contract versions are rejected, never defaulted.
- A closed incident is modified only via new journal events.
- External action requires authorized scope.
- Secrets never appear in receipts.
- A discovered provider is not automatically executable.
- Doctor diagnoses keep provenance.
- Rule changes never silently rewrite historical results.
