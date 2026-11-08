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

```bash
# backup (database + evidence)
sqlite3 .api-sentinel/sentinel.db ".backup backup/sentinel-$(date +%Y%m%d).db"
cp -r .api-sentinel/blobs backup/blobs/

# restore
cp backup/sentinel-YYYYMMDD.db .api-sentinel/sentinel.db
cp -r backup/blobs/ .api-sentinel/blobs/
```

Restore the DB **and** the blob directory together — evidence rows
reference blob hashes, so a DB without its blobs yields unverifiable
references. Backup/restore is a tested path (Cycle 9 gate); run it
before any upgrade that carries migrations.

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

## Upgrade / downgrade policy

Migrations are versioned and applied forward only. Before upgrading:
backup. Before downgrading: **restore the pre-upgrade backup** — running
an older binary against a newer schema is unsupported.
