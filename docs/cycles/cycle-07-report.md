# Cycle 7 — AWS Read-Only Observability

Adds the first remote connector: a read-only CloudWatch/API Gateway
adapter under `integrations/aws/`, behind a mechanical read-only
allowlist, an explicit account/region scope verified by STS preflight,
and per-source budgets on every egress knob from the cost model
(docs/07 §16.3, docs/14, ADR-013/014). The domain core never knows AWS
exists — the adapter emits standard `RuntimeObservationV1` JSONL into
the *existing* ingestion pipeline, so dedup, quarantine, quality
assessment and checkpoints are reused, never forked.

## Verification tails

```text
$ python -m pytest -q
331 passed in 17.61s

$ python -m ruff check .
All checks passed!

$ python -m mypy src
Success: no issues found in 93 source files
```

### Simulated CLI smoke (`--simulate lab013`, zero egress)

```text
$ api-sentinel aws collect --source-id src_… --window 30m \
    --end 2030-01-01T12:00:00+00:00 --simulate lab013 --json
{"outcome": "partial", "detail": "collection completed with named losses",
 "account_masked": "1234****9012", "account_hash": "sha256:3ad280755990fecc",
 "region": "us-east-1", "requested_queries": 2, "answered_queries": 2,
 "observations": 6, "accepted": 6, "calls": 4, "pages": 2, "datapoints": 6,
 "throttled_retries": 2, "retried_calls": 2, "bytes_processed": 775,
 "estimated_cost_usd": 0.00008,
 "messages": ["data points were omitted for part of the requested window"],
 "receipt_path": "…/receipts/rcpt_aws_….aws.receipt.json"}

# same window re-collected → identical content → pipeline dedup
$ api-sentinel aws collect … (repeat) → {"accepted": 0,
  "skipped_duplicates": 6, "observations": 6, "datapoints": 6}

$ api-sentinel aws discover --region us-east-1 --simulate lab013 --json
{"account_masked": "1234****9012", "region": "us-east-1", "calls": 4,
 "endpoints": [{"api_id": "restapi123", "api_type": "REST", "stage": "prod"},
               {"api_id": "httpapi456", "api_type": "HTTP",
                "stage": "$default"}]}

# receipt on disk — masked + hashed scope, no ARN, no credentials
$ cat …/receipts/rcpt_aws_….aws.receipt.json
{"account_masked":"1234****9012","account_hash":"sha256:3ad280755990fecc",
 "outcome":"partial","calls":4,"pages":2,"datapoints":6,
 "throttled_retries":2,"estimated_cost_usd":0.00008,…}
$ grep -c "AKIA\|arn:aws\|123456789012" receipts/*.json → 0, 0
```

### LAB-013 (throttling → backoff → pagination → partial)

`tests/unit/test_aws_readonly.py::TestCollectAwsSource::
test_lab013_end_to_end` drives the canned account boundary
(`simulated.lab013`): `GetMetricData` throttles twice (counted on the
meter → receipt), a 2-page chain is concatenated, a `Messages` page
marks the run `partial` with the message carried into `unknowns` — all
with injected `sleep`/`jitter`/`monotonic` (offline, wall-clock-free).
`throttle-forever` exercises the bounded `AwsThrottled` refusal with a
refusal receipt still written.

## Acceptance criteria

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Profile/role discovery uses existing profiles + temporary creds; identity verified before collection; explicit account/region scope | ✅ | `client.resolve_session` profile → `assume_role` → default chain; `identities.verify_identity` STS preflight runs before any metric call; account/region mismatch = named refusal (`TestIdentityPreflight`); secret-shaped config keys refused pre-session (`TestCredentialBoundary`) |
| 2 | `GetMetricData` multi-query, `NextToken` pagination, quotas, partial results | ✅ | `cloudwatch.CloudWatchCollector` chunks queries (`max_metrics_per_query`), follows `NextToken` with page cap, honors `Messages`/non-complete `StatusCode` → PARTIAL + unknowns (`test_three_page_pagination_concatenated`, `test_response_messages_mark_partial`) |
| 3 | REST + HTTP API discovery; Count/4xx/5xx/Latency/IntegrationLatency mapped; per-route detail opt-in cost-bearing | ✅ | `apigateway.discover_gateways` (REST `get_rest_apis`/`get_stages`/`get_resources`, HTTP `get_apis`/`get_stages`/`get_routes`); `REST_API_METRICS`/`HTTP_API_METRICS` maps incl. `IntegrationError`; `aws.failure_scope` keeps gateway-vs-app distinct; `detailed_metrics` opt-in bounded by `max_detailed_routes_per_api` (`TestApiGateway`) |
| 4 | Checkpoints per source/account/region allow resuming without re-fetch | ✅ | `_write_checkpoint` stores `last_window_end` + scope note per source; `--resume` picks it up (`test_checkpoint_written_and_resume_respected`); batch-level dedup already prevents duplicated points |
| 5 | Every cost-model knob configurable | ✅ | `AwsCollectBudget` fields: `max_metrics_per_query`, `max_datapoints`, `max_lookback_minutes`, `max_calls_per_cycle`, `max_bytes_processed`, `concurrency` (pinned 1), `max_runtime_seconds`, `max_retries`, `max_pages_per_call`, `max_stored_bytes`, `max_estimated_cost_usd`, `max_detailed_routes_per_api`; `budget.<knob>` source config + `--max-*` CLI flags; collection frequency = caller's schedule (checkpoint + lookback bound the window) |
| 6 | Throttling → bounded backoff; LAB-013 proves backoff+pagination+budget under simulated throttling | ✅ | `call_with_backoff` classifies `THROTTLE_CODES` → jittered exponential wait; `lab013` script = 2 throttles → 2-page success + `Messages`; receipt records `throttled_retries`/`retried_calls` |
| 7 | Retries carry jitter and are capped by budget | ✅ | `JitteredBackoff.delay = min(cap, base·2ⁿ) + jitter·base` (sleep/jitter injected); `max_retries` cap → `AwsThrottled` (`test_backoff_delay_is_jittered_exponential_and_capped`, `test_throttling_exhaustion_raises_aws_throttled`) |
| 8 | Pagination handled completely on every list/metrics API | ✅ | `get_metric_data` (`NextToken`), `get_rest_apis`/`get_resources` (`position`), `get_apis`/`get_routes` (`NextToken`) — all looped with `max_pages_per_call` cap → `truncated` + named unknown, never silent drop (`test_page_cap_truncates_with_named_unknown`, `test_rest_api_pagination_via_position`) |
| 9 | Documented least-privilege IAM; no AdminAccess; no permanent keys; zero mutating calls verified by test/scan | ✅ | IAM policy JSON documented in `docs/15-operations-runbook.md` (sts:GetCallerIdentity, cloudwatch:GetMetricData+ListMetrics, apigateway:GET only); `prove_read_only()` + `TestReadOnlyAllowlist` (18 cases) prove mutations refuse pre-transport; paginator/waiter escape hatches blocked |
| 10 | Redaction before persistence; audit logs credential-free | ✅ | `write_collect_receipt` runs `redact()`; `test_credential_config_refused_before_any_aws_call` asserts zero transport calls + no `AKIA…` in any receipt; receipt carries masked account + sha256 digest only |
| 11 | Cost indicators: measurable API usage + estimated cost per run | ✅ | receipt/report carry `calls`, `pages`, `datapoints`, `bytes_processed`, `throttled_retries`, `estimated_cost_usd` ($0.01/1k metrics-requested heuristic, documented in `budgets.py`) |
| 12 | Sandbox lab over simulated AWS APIs covering throttling/pagination/partial; real-account homologation separately | 🟡 simulated ✅ / homologation pending | `simulated.py` canned boundary + `--simulate`/`API_SENTINEL_AWS_SIMULATE` lab-only injection; 51 unit + 3 CLI tests offline; **real-account homologation NOT run — no credentials in the dev environment** (checklist below) |
| 13 | Read-only verification: test proves no mutating call on any code path | ✅ | `prove_read_only()` executes the boundary against a wiretap transport (6 mutating/escape attributes refused, zero transport hits, allowlisted call reaches transport) and gates `api.aws-readonly-observability` → AVAILABLE in `capabilities.py`; `test_*_refused_pre_transport` × mutation names incl. PascalCase spellings |

## Changed files

- **Adapter (new):** `src/api_sentinel/integrations/aws/{__init__,errors,
  client,cloudwatch,apigateway,identities,budgets,simulated,collect}.py`
  — `ReadOnlyClient` allowlist (the security boundary), profile→role→
  default-chain `resolve_session`, STS `verify_identity` with masked
  account + sha256 digest, `AwsCollectBudget`/`BudgetMeter`/
  `JitteredBackoff`/`call_with_backoff`, `CloudWatchCollector`
  (chunked multi-query `GetMetricData`, NextToken, partials, units),
  `discover_gateways` (REST `position`-pages + HTTP `NextToken`-pages),
  `apigateway_metric_queries` (failure-scope attributes), canned
  `SimulatedAwsClient` + `lab013`/`throttle_forever` scenarios,
  `CollectAwsSource` orchestrator (preflight → plan → collect → ingest
  via the existing pipeline → checkpoint/audit/redacted receipt).
- **Surface:** `cli/__init__.py` — `aws` sub-app (`aws collect
  --source-id/--window/--end/--resume/--simulate/--max-*`,
  `aws discover --profile/--region/--account-id/--role-arn/--simulate`);
  repeatable `sources --config KEY=VALUE`; lab scenario anchored to
  `--end` for deterministic re-collection; `capabilities.py` —
  `api.aws-readonly-observability` AVAILABLE ⇔ `prove_read_only()`
  passes **and** boto3 installed, else DECLARED with the reason.
- **Docs:** `docs/15-operations-runbook.md` — AWS section: credential
  chain rules, least-privilege IAM policy JSON, register/collect,
  budget knobs, simulated lab, homologation pointer.
- **Spec:** `factory/specs/active/cycle-07-…` — ACs ticked (the
  homologation AC stays open with a note).
- **Tests:** `tests/unit/test_aws_readonly.py` (51 tests — allowlist
  ×18, credential boundary, STS preflight, backoff, collector
  pagination/partials/caps, unit mapping, REST-vs-HTTP, end-to-end
  LAB-013, dedup, checkpoint/resume, credential refusal pre-transport,
  throttle-forever refusal receipt); `tests/integration/test_cli.py`
  (+3 simulated CLI tests); `tests/unit/test_integrations_boundary.py`,
  `tests/integration/test_api_surface.py` (updated for proof-backed
  AVAILABLE state — forge seam still routes no action for the
  capability, named refusal verified).

## Mismatches & honest gaps

- **Real-account homologation is pending — NOT run.** No AWS
  credentials exist in the dev environment; the spec requires a
  recorded authorized test-account run, so that half of AC-12 stays
  unticked. Checklist below; result lands under `factory/runs/` when an
  authorized account is available.
- **`aws collect` sleeps for real on throttling** (bounded by
  `max_retries` × ≤8s+base each — ≤ ~33s worst case); the injected
  `sleep`/`jitter` knobs exist for tests/lab only, matching the
  deterministic-core rule (the wall-clock lives at the adapter edge).
- **`concurrency` is pinned to 1** — sequential collection only; the
  knob exists to be *validated*, so enabling parallelism later is an
  explicit budget change, never silent.
- **No CloudWatch Logs/X-Ray/payloads** (ADR-014 out-of-scope): metrics
  and API inventory metadata only; `operation_id` is never synthesized —
  unmapped dimensions stay UNKNOWN on the observation.
- **Estimated cost is a documented heuristic** ($0.01/1k metrics
  requested), not a billing figure — receipts label it `estimated_*`.
- **WebSocket APIs are skipped by name** (`websocket_api_skipped:<id>`
  unknown) — different metric shape, out of scope.
- **boto3 has no shipped type stubs** — the two import sites carry
  `# type: ignore[import-untyped]` comments naming the gap.

## Pending real-account homologation checklist

Run once against an authorized test account (record output under
`factory/runs/cycle-07-homologation/`):

1. Provision the docs/15 IAM policy on a dedicated read-only
   principal (SSO/Identity Center or assumed role — never a permanent
   access key).
2. `api-sentinel aws discover --profile <p> --region <r> --account-id
   <12d>` → verified masked account + REST/HTTP inventory.
3. Register a `cloudwatch` source with `queries_json` for real
   `AWS/ApiGateway` metrics; `aws collect --window 30m --json` →
   report fields + receipt.
4. Re-run the same window → `accepted=0`, `skipped_duplicates>0`
   (dedup through the pipeline).
5. `--resume` after a first run → `resumed_from` equals the previous
   `last_window_end`.
6. Force a throttle/budget path (`--max-calls 1`) → named
   `budget_exceeded`/`AwsThrottled` terminal state + refusal receipt.
7. Negative scope: `--account-id <different>` → named `account
   mismatch` refusal before any metric call.
8. Verify receipts/audit/logs contain no access keys, session tokens
   or full ARNs (`grep` sweep).
9. Confirm zero mutating calls in CloudTrail for the read-only
   principal during the whole run.

Commit: see `git log -1` (cycle commit, message
`Cycle 7: AWS read-only observability connectors`).
