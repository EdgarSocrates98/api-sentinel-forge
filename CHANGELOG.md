# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses Semantic Versioning.

## [Unreleased]

### Cycle 10 — cross-forge integration, operational UX & production readiness

Wave-closed homologation loop (specs: `factory/specs/archive/cycle-10-*`;
evidence: `docs/cycles/cycle-10/`). Package version stays `0.1.0`; the
planned tag after merge is `v0.1.0-rc.2` (rc line per the versioning
policy — see `docs/cycles/cycle-10/18-release-readiness.md`).

#### Added

- `/v1` real authentication: workspace bearer token
  (`secrets.token_urlsafe(32)`, `hmac.compare_digest`, env → stdin →
  file precedence, optional TTL rotation) + HMAC-signed integrated
  principal (±300 s skew, single-use nonce, workspace match). Serving
  is never credential-less (F007).
- `api-sentinel mcp serve` — read-only stdio MCP server (`mcp` extra):
  `sentinel_list_services`, `sentinel_capabilities`, `sentinel_health`,
  `sentinel_list_incidents` (F012).
- `integrations/atlas/store_adapter.py` — `DbStructuralContext`
  consumes a real Code Atlas SQLite index read-only, READY-snapshot
  gated, exact-identity resolution only (F011).
- `adapters/apisentinel` — stdlib-only `forge/v1` provider adapter for
  the-forge; all 8 declared capability/action pairs execute
  (F010/F015/F018).
- Ingestion batching: `get_many` / `idempotency_keys` /
  `overlapping_sources_in_ranges` repository ports — dedup/service/
  overlap point queries collapsed to constant-size batches (F006).

#### Fixed

- Stale `ProjectContext` import in the Doctor ACL (moved to
  `forge_doctor_api.core.context` at doctor 0.3.1); real in-process
  delegation proven — 10 findings / 9 unknowns (F009, F003).
- `InvestigationHandoffV1` requiredness aligned with the published
  schema — `payload`/`constraints` required in the model (F014).
- `handoff ingest` requires explicit `handoff_version` — arbitrary JSON
  no longer ingests as a Doctor bundle (F022).
- ForgeManifest declares only executor-backed pairs (F015);
  ExecutionResult findings re-emit evidence refs as declared Evidence
  records (F018).
- Lint debt in `scripts/cov_core.py` (F013); README Cycle-1 staleness
  (F001); demo exporter's contract-illegal `measurement.temporality`
  (F023).

#### Security

- Self-declared `X-Sentinel-Role` is dead: every `/v1/*` route except
  `/v1/health` requires a verified credential; `401` + problem+json on
  failure; CORS deny-by-default; forwarded headers never consulted.
- Handoff/file boundary hardened: correlation ids end-to-end,
  transport_state ≠ execution_state, path-traversal guards
  (`resolve_under_root`), bounded reads (32 MiB), ingest correlation
  checks.
- §12 battery: 40/40 controls held — `evidence/security-results.json`.

#### Documentation

- `docs/cycles/cycle-10/` — full wave record (00–18) + consolidated
  evidence manifest; findings ledger F001–F024 final states;
  release-readiness gates A–G scored; final report A–K.
- `docs/16-compatibility-matrix.md` refreshed with verified versions.
- Portal counterpart: `forge-portal@cycle-10-sentinel` proves
  `/api-operations` on live Sentinel `/v1` data (19/19 browser steps).

## [0.1.0-rc] - 2026-11-09

Release candidate — cycles 0–9 consolidated. `0.1.0` stays the package
version; the RC label lives here only, per the versioning policy.

### Added

- Cycle 0 foundation: canonical docs (`docs/00`–`17`), ADR-001…018,
  versioned JSON Schemas under `contracts/v1/`, capability-ownership
  matrix, threat model, E2E strategy and technical plan.
- Cycle 1 core: installable `api_sentinel` package; typed domain models
  (service/environment/operation identity, telemetry sources, observation
  batches, evidence artifacts); versioned SQLite migrations and
  repositories; workspace control (project/user scope); evidence storage
  with SHA-256 integrity; structured secret-free logging; secret redaction
  helpers.
- CLI `api-sentinel` with `install`, `doctor`, `status`, `capabilities`,
  `services`, `sources`, `workspace`, `version` and a `--json` mode;
  documented exit codes.
- FORGE ecosystem manifests: `forge.json`, `forge.agentic.json`, and a
  `forge-knowledge` package prepared for registration in `the-forge`;
  loop-factory specs for Cycles 2–9 under `factory/specs/`.
- Cycle 2 telemetry ingestion: JSONL/OTLP-JSON importers, normalization
  pipeline, batch idempotency + per-source checkpoints, bounded quarantine
  (redacted payloads, truncation flags), ingestion jobs and provenance.
- Cycle 3 runtime health: honest `AVAILABLE`/`UNAVAILABLE`/`PARTIAL`/
  `UNVERIFIED`/`UNKNOWN` states, SLI/SLO definitions, error-budget
  evaluation, staleness conditions.
- Cycle 4 detection + incidents: deterministic detectors with versioned
  rules, anomaly dedup, append-only `incident_events` lifecycle, evidence
  requirements.
- Cycle 5 cross-forge intelligence: Atlas/Lens/Doctor/data-sentinel
  integration seams with named `AVAILABLE`/`UNAVAILABLE`/`UNVERIFIED`
  states and handoff-file envelopes.
- Cycle 6 portal API: FastAPI `serve` on loopback, `X-Sentinel-Role`
  placeholder scopes, query/investigation/handoff routes, portal views.
- Cycle 7 AWS read-only observability: optional `aws` extra, CloudWatch/
  API Gateway collectors, simulated offline lab (LAB-013), receipts.
- Cycle 8 agentic investigation: hypothesis lifecycle, typed verification
  plans, change intelligence, governed agent sessions via Bridge
  handoffs.
- Cycle 9 production hardening: `backup create/restore` (verified
  manifests, refusal without `--force`), `retention` policy enforcement
  (dry-run default, evidence protection, journaled receipts), Sentinel
  self-observability (`sentinel` block in `doctor` + additive `/v1/health`
  field), scale benchmark (`lab/bench/`), authorization matrix
  (LAB-016), degradation matrix, contract compatibility matrix,
  backup/restore recovery proof (LAB-014), socket-blocked offline test
  proof, and Windows+Linux CI.
