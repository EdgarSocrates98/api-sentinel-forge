# 15 — Operations Runbook

Operational guide for a local-first Sentinel install. Commands marked
*proposed* follow the §17.2 command surface and may shift until the
naming audit lands (see 12-installation-and-distribution).

## Install & verify

```bash
api-sentinel install          # guided wizard: profile, scope, Portal detection
api-sentinel doctor           # environment sanity check (smoke command)
api-sentinel status           # workspace state, sources, integrations
api-sentinel capabilities     # real, currently available capabilities
```

`doctor` is the registered smoke command after install; `capabilities`
reports what is actually usable — a discovered provider is not
automatically executable.

## Workspace layout

```
.api-sentinel/
├── sentinel.db        # SQLite WAL — metadata, state, aggregates, journal
├── config.yaml        # workspace configuration and source registrations
├── blobs/             # content-addressed evidence artifacts
└── receipts/          # handoff/execution receipts
```

## Register a service and source

```bash
api-sentinel services add payments-api --environment production
api-sentinel sources add --type jsonl --service payments-api \
    --path ./telemetry.jsonl
```

Service identity is canonical (see 03-domain-model): friendly names are
never the unique key — include environment/namespace so two same-named
services are not merged.

## Ingest a file

```bash
api-sentinel ingest ./telemetry.jsonl      # JSONL normalized or supported OTLP JSON
api-sentinel ingest ./telemetry.jsonl --json   # machine-readable result
```

Ingestion is idempotent: re-ingesting the same file does not duplicate
points. Batches failing schema validation go to quarantine, not into
aggregates.

## Read incidents

```bash
api-sentinel incidents                     # active + historical
api-sentinel incident show INC-001         # detail: status, hypotheses, evidence, timeline
```

## Backup & restore

The `backup` command group (Cycle 9) produces verifiable snapshots: a
consistent `.backup`-API copy of `sentinel.db`, the `blobs/` content-
addressed store, `config.yaml`, and a `manifest.json` recording app
version, per-table row counts, SHA-256 checksums and the snapshot time.

```bash
# create a verified snapshot under <workspace>/backups/
api-sentinel backup create --workspace .api-sentinel --json

# restore — refuses while live data exists
api-sentinel backup restore --from <workspace>/backups/backup-<ts> \
    --workspace .api-sentinel

# restore over a live workspace — the live files are MOVED ASIDE
# (sentinel.db.prerestore-<ts>), never silently deleted
api-sentinel backup restore --from <backup-dir> --workspace .api-sentinel --force
```

Restore verifies every manifest checksum before touching the workspace
(`integrity: ok` in the report) and runs `PRAGMA integrity_check` +
`foreign_key_check` on the restored database. A checksum mismatch aborts
the restore without writing. Restore the DB **and** the blob directory
together — evidence rows reference blob hashes, so a DB without its
blobs yields unverifiable references.

The full recovery path (backup → corrupt/delete → restore → identical
service/source/evidence/query state) is proven by LAB-014
(`tests/integration/test_backup_restore.py`). Run a backup before any
upgrade that carries migrations.

## Retention

Retention enforces the docs/10 policy through `api-sentinel retention`.
It is **dry-run by default** — candidates are counted and reported,
nothing is deleted — and journals a receipt under `receipts/` plus an
`operations.retention` audit row when applied.

```bash
# preview the policy decision (exit 0, "apply": false)
api-sentinel retention --workspace .api-sentinel --json

# apply deletions, receipt + audit row written
api-sentinel retention --workspace .api-sentinel --apply --json

# per-tier overrides (also configurable in config.yaml under `retention:`)
api-sentinel retention --apply --observations-days 14 --incidents-days 365
```

Policy tiers (docs/10): detailed observations 7d, 1-minute-class
aggregates 30d, hourly-class 90d, quarantine 30d, terminal incidents
180d, evidence 180d, audit events **never** auto-purged unless
`--audit-days` is passed explicitly.

The hard rule (ADR-015): evidence referenced by a live incident,
anomaly, investigation or dependency observation is **never** deleted —
protection follows the reference, not the age. Evidence owned by a
*purged* incident loses protection with its incident. Orphan `blobs/`
files with no evidence row are reported (`orphan_blob_files`), never
silently dropped. Proven by `tests/integration/test_retention.py`.

## Connector-failure triage

Every integration reports a named state — `AVAILABLE`, `UNAVAILABLE`,
`UNVERIFIED` — never simulated success (`api-sentinel integrations`,
`api-sentinel doctor --json`, `/v1/health`).

| Symptom | First checks |
| --- | --- |
| `code-atlas-forge: unavailable` | `integrations.atlas.export` set in `config.yaml`? file readable? |
| `forge-lens: unavailable` | `forge_lens` importable? `integrations.lens.refs` exported manifest path valid? |
| `forge-doctor-api: unverified` | handoff mode: envelope emitted under `handoffs/` — is the peer running? |
| `forge-agent-bridge: unverified` | `forge_agent_bridge` importable? `agent_sessions/` envelopes pending? |
| `aws` errors | `api-sentinel aws` receipts under `receipts/` record the named failure |

Core query/detect/incident surfaces stay available through every
integration failure — proven by the degradation matrix
(`tests/integration/test_degradation_matrix.py`). A telemetry gap
produces `UNVERIFIED`/`UNKNOWN`, never fabricated health.

## Quarantine triage

Items that fail validation land in `ingestion_quarantine` — bounded,
redacted, never silently dropped. Check depth via `api-sentinel doctor
--json` (`sentinel.quarantine.depth`) or `/v1/health`.

```bash
# inspect quarantined entries (reason, batch, truncated flag)
sqlite3 .api-sentinel/sentinel.db \
  "SELECT item_id, batch_id, reason, payload_truncated FROM ingestion_quarantine;"

# drain via retention once diagnosed (default 30d tier)
api-sentinel retention --apply --quarantine-days 30 --json
```

A growing quarantine depth means the producer is shipping malformed or
oversized envelopes — check `reason` (schema violation vs size bound)
before touching the source.

## Budget exhaustion

Bounded refusal is the design contract: oversized batches, quarantine
payloads and connector calls refuse deterministically rather than
degrading silently.

| Budget | Where | Exhaustion behavior |
| --- | --- | --- |
| `max_batch_bytes` | ingest policy | `sentinel.validation` error, batch refused |
| quarantine payload bytes | ingest policy | payload truncated + `payload_truncated` flag |
| AWS calls/pages | `aws` connector budgets | named failure (`throttled`, `budget_exhausted`) in receipt |
| investigation time | `budget_seconds` constraint | hypothesis set returns with `budget_exhausted` marker |

Check `api-sentinel doctor --json` → `sentinel` for storage size,
oldest/newest observation and per-source staleness before raising a
budget; retention (`above`) is the correct lever for storage growth.

## Corrupt-database recovery

1. Stop writers (`serve`, collectors, ingest jobs).
2. Copy the damaged file aside: `cp sentinel.db sentinel.db.corrupt`.
3. `sqlite3 sentinel.db.corrupt ".recover" | sqlite3 sentinel.db.new`
   (or restore from the last good `.backup`).
4. Swap in the recovered file; run `PRAGMA integrity_check;` and
   `foreign_key_check;`.
5. Run the orphan-blob sweep to reconcile `evidence_artifacts` rows vs
   `blobs/` contents; orphaned artifacts are reported, not silently
   dropped.

## Logs

Structured logs, secret-free by construction (Cycle 1 deliverable):
emitted by the CLI/serve process alongside the workspace; audit events
persist to the `audit_events` table. Credentials never reach logs,
receipts or the `blobs/` store.

## Common failure states

| State shown | Meaning | Not |
| --- | --- | --- |
| source stale | source stopped sending observations | service is down |
| doctor unavailable | specialized diagnosis not concluded | detection stopped |
| atlas unavailable | no verified structural dependencies | no investigation |
| portal down | UI unreachable | CLI/local API broken |
| bridge down | no agentic sessions | no deterministic investigation |
| telemetry gap | missing data in window | outage |

A telemetry gap is **not** an outage; a missing source never produces
fabricated positive health (§24.1 failure strategy).

## AWS read-only observability (Cycle 7, opt-in)

The `aws` extra (`pip install api-sentinel-forge[aws]` → boto3) adds a
read-only CloudWatch/API Gateway connector. The domain core never sees
AWS; the adapter emits standard `RuntimeObservation` envelopes into the
existing ingestion pipeline.

### Credentials — held by the environment, never by the Sentinel

Session resolution order: `--profile`/`config profile` → `config
role_arn` (assumed via STS) → the AWS default credential chain
(SSO/Identity Center, env vars, workload identity). Secret-shaped keys
(`aws_access_key_id`, `aws_secret_access_key`, …) in source config are
refused **before** any session work — credentials are never embedded,
persisted or logged.

### Least-privilege IAM permission set

Attach this policy to the read-only principal (replace ACCOUNT/REGION —
or keep `*` on the resource ARN since the actions are read-only):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SentinelIdentity",
      "Effect": "Allow",
      "Action": "sts:GetCallerIdentity",
      "Resource": "*"
    },
    {
      "Sid": "SentinelCloudWatchMetrics",
      "Effect": "Allow",
      "Action": ["cloudwatch:GetMetricData", "cloudwatch:ListMetrics"],
      "Resource": "*",
      "Condition": {"StringEquals": {"aws:RequestedRegion": "REGION"}}
    },
    {
      "Sid": "SentinelApiGatewayInventory",
      "Effect": "Allow",
      "Action": "apigateway:GET",
      "Resource": [
        "arn:aws:apigateway:REGION::/restapis",
        "arn:aws:apigateway:REGION::/restapis/*",
        "arn:aws:apigateway:REGION::/apis",
        "arn:aws:apigateway:REGION::/apis/*"
      ]
    }
  ]
}
```

No `AdministratorAccess`, no `*` action, no write verb — the adapter's
`ReadOnlyClient` allowlist (`get_*`/`list_*`/`describe_*`/`query`)
independently refuses any non-read call before it reaches the
transport, verified mechanically by `prove_read_only()`.

### Register and collect

```bash
api-sentinel sources --register cloudwatch --service-id svc_… \
    --config region=us-east-1 \
    --config account_id=123456789012 \
    --config 'queries_json=[{"namespace":"AWS/ApiGateway","metric_name":"Latency","stat":"Average","unit":"Milliseconds","period_seconds":300}]'

api-sentinel aws collect --source-id src_… --window 30m --json
api-sentinel aws discover --profile obs-readonly --region us-east-1 --json
```

`kind=api-gateway` sources discover REST **and** HTTP APIs (stages,
opt-in per-route detail via `detailed_metrics=true` — cost-bearing and
bounded by `budget.max_detailed_routes_per_api`).

### Budgets

Every egress knob is configurable per source (`--config
budget.<knob>=value`) or per run (`--max-*` flags):
`max_metrics_per_query`, `max_datapoints`, `max_lookback_minutes`,
`max_calls_per_cycle`, `max_bytes_processed`, `concurrency` (pinned
1), `max_runtime_seconds`, `max_retries`, `max_pages_per_call`,
`max_stored_bytes`, `max_estimated_cost_usd`. Exhaustion is a named
refusal or a PARTIAL result with unknowns — never silent truncation.

### Simulated lab (offline, LAB-013)

`--simulate lab013` (or `API_SENTINEL_AWS_SIMULATE=lab013`) injects the
canned account boundary — throttling ×2 → backoff → 2-page paginated
result ending in `Messages` (PARTIAL). `throttle-forever` exercises the
bounded-refusal path. Simulated runs never perform egress; receipts are
written identically to real runs.

### Homologation

Real-account homologation is pending (no credentials in the dev
environment); checklist lives in `docs/cycles/cycle-07-report.md`.

## Upgrade / downgrade policy

Migrations are versioned, checksum-recorded (`schema_migrations`) and
applied forward only — Cycle 9 verified apply-once behavior on a
populated workspace (`tests/integration/test_storage.py`) and a full
backup/restore round-trip on populated state (LAB-014).

Before upgrading: `api-sentinel backup create`. Before downgrading:
**restore the pre-upgrade backup** (`api-sentinel backup restore
--force`) — running an older binary against a newer schema is
unsupported, and no reverse migrations exist. The irreversible step is
the schema migration itself; that is why the pre-upgrade backup is the
documented rollback path, not a `--downgrade` flag.

## Continuous verification

`.github/workflows/ci.yml` (Cycle 9) gates every push/PR on
Windows + Linux × Python 3.12 through the same local checks:
`ruff check .`, `mypy src`, `pytest -q`, `uv build`, and a post-install
`api-sentinel doctor` smoke on the built wheel. Local mirror:

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy src
.venv/Scripts/python.exe -m pytest -q          # offline: sockets blocked
.venv/Scripts/python.exe -m pytest -m slow -q  # scale bench gate (opt-in)
uv build
```
