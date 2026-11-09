---
cycle: 10
deliverable: aws-readonly-validation
wave: 4
---

# Cycle 10 — AWS Read-Only Validation

**Wave 4, EXTERNAL_REAL + SIMULATED negatives.** Bounded, authorized,
read-only validation of the AWS integration against the homologation
account, plus the offline negative battery. No credentials are persisted
anywhere in the repository, receipts, or logs — session credentials were
resolved via `aws configure export-credentials` into environment
variables only, and every receipt carries masked account + hash.

## Preflight (gate)

`aws sts get-caller-identity --profile sparkforge` → account
`7025****1161` (hash `sha256:51e88bde2c83d9b4`), matching the expected
homologation account `702561771161` → the **real bounded path executed**
(not the simulated fallback). Region `us-east-1`.

Mechanism note: the `sparkforge` profile uses `login_session` credentials
(AWS CLI v2 `aws login`), which botocore cannot resolve without
`botocore[crt]`. Credentials were exported by the AWS CLI itself into
env vars for the subprocess — Sentinel's default credential chain picked
them up; nothing was written to disk or source config.

## Mechanical read-only proof

`prove_read_only()` — **PASSED**: six mutating/escape attributes
(`terminate_instances`, `put_metric_data`, `delete_alarms`,
`create_api_key`, `get_paginator`, `meta`) refused pre-transport with
zero calls issued; allowlisted `describe_alarms` reached the transport.

## Real flows (EXTERNAL_REAL)

| id | flow | outcome |
|---|---|---|
| AWS-RT-01 | `aws discover` (account check + API listing) | found `sentinel-demo-api` (`whbzatkr64`, HTTP, `$default`) — 3 calls |
| AWS-RT-02 | `aws collect` api-gateway source, 30 m | `empty` — no datapoints (honest zero) |
| AWS-RT-03 | `aws collect` api-gateway source, 24 h | `partial` — 4 datapoints → 4 observations accepted (`batch_01M70DVTYJ8BSVP403GXVY4RQZ`) |
| AWS-RT-04 | `aws collect` 5 m | `empty` |
| AWS-RT-05 | `aws collect` 5 m, source scoped `eu-west-1` | `empty` + `no_queries` — wrong region degrades to honest zero-query empty, not a fake error |

Budgets: ≤30 calls, ≤5 000 datapoints, ≤90 s runtime, ≤1 440 m lookback.
Observed: 15 calls total, ~$0.00018 estimated cost across all real runs.
All runs wrote redacted receipts (`lab/run/aws-rt/receipts/`, embedded in
`evidence/aws-validation.json` with sha256).

## Negative controls

| id | case | truth | result |
|---|---|---|---|
| AWS-NEG-01 | wrong `--account-id` on real creds | EXTERNAL_REAL | `integration_refused` — account mismatch, masked on both sides |
| AWS-NEG-02 | `--simulate lab013` | SIMULATED | `partial` — 6 obs ingested, 2 throttled retries, canned account `1234****9012` |
| AWS-NEG-03 | `--simulate throttle-forever` (cloudwatch source) | SIMULATED | `sentinel.aws_throttled` — GetMetricData throttled past retry budget |
| AWS-NEG-04 | `--simulate throttle-forever` (api-gateway source) | SIMULATED | `integration_refused` — `UnscriptedOperation` (script covers GetMetricData only) |

## SLO / anomaly evidence from the collected data

`slo evaluate` (24 h window, availability target 0.99) → `MET`, SLI
1.0 — the per-source breakdown keeps it honest: the real api-gateway
source carries observations but `eligible=0` (gateway metrics have no
request-plane — the SLI does not fabricate one), while the eligible
count comes from the simulated-source observations. `detect` over the
same window → `source_silence` anomaly **fired on the real source**
(`src_01M70DV3GED…`, silent 3 788 s — collection had stopped; reported
as "service state unknown", never proof of outage). All other rules
clean. Evidence: `aws-validation.json.slo_and_detection_evidence`.

## Honesty notes

- `empty`/`partial` are real transport outcomes, never relabeled.
- The 24 h `partial` outcome names its losses (`status:Complete`
  unknowns for the six `AWS/ApiGateway` metrics) — datapoints that
  arrived are ingested; gaps are listed, not smoothed over.
- Simulated cases use the lab-only `--simulate` transport; their
  receipts carry the canned account `1234****9012`, keeping simulated
  and real evidence disjoint.
- No write/mutation API was ever invoked; `prove_read_only` plus the
  allowlist refusal log is the mechanical evidence.

## Evidence

- `evidence/aws-validation.json` — preflight, proof, all flows,
  negatives, embedded receipt bodies + hashes.
- `lab/run/aws-rt/receipts/*.aws.receipt.json` — raw redacted receipts.
