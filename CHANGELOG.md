# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses Semantic Versioning.

## [Unreleased]

## [0.1.0] - 2026-11-08

### Added

- Cycle 0 foundation: canonical docs (`docs/00`–`17`), ADR-001…018,
  versioned JSON Schemas under `contracts/v1/`, capability-ownership matrix,
  threat model, E2E strategy and technical plan.
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
  `forge-knowledge` package prepared for registration in `the-forge`.
- Loop-factory specs for Cycles 2–9 under `factory/specs/inbox/`.
