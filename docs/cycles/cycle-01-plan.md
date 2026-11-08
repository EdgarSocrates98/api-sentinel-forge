# Cycle 1 — Plan: Core, Identity & Persistence

Implement the real skeleton — no mandatory external services.

## Deliverables

| Deliverable | Notes |
| --- | --- |
| Installable Python package | `api-sentinel-forge` / `api_sentinel`, `pip install -e .` |
| Initial CLI | `doctor`, `status`, `capabilities`, `install` |
| Typed models | services, environments, operations, releases |
| Telemetry-source contract | `api-sentinel/TelemetrySource/v1` in `contracts/v1/` |
| SQLite repositories | one writer per table; ids across contexts |
| Versioned migrations | `migrations/` — forward-only, integrity-checked |
| Evidence persistence | content-addressed `blobs/` + `evidence_artifacts` |
| Workspace control | `.api-sentinel/` root, config, isolation |
| Structured logs, no secrets | secret-free by construction |
| Domain + storage tests | deterministic; injected clock/id |
| Minimal persistence lab | first scenario under `lab/` |

Implementation lands in `src/api_sentinel/` per `docs/02`:
`domain/` (inventory, telemetry, evidence), `application/` (use cases,
ports), `infrastructure/` (storage, security), `cli/` — boundaries
respected even with the module set still small.

## Exit gate

Create services, register sources, store references and **recover state
after restart without loss or corruption**.

Gate evidence: register a service + source via CLI, persist an evidence
artifact, kill/restart the process, then `status`/`services`/`sources`
must reproduce identical state; DB passes `integrity_check` and
`foreign_key_check`.

## Explicitly out of scope

External connectors, OTel collectors, SLO evaluation, detection,
incidents workflow, Forge adapters, Portal UI — each has its own cycle
(see `cycle-00-report.md` open items).
