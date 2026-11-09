# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses Semantic Versioning.

## [Unreleased]

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
