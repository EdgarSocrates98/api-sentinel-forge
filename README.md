# API Sentinel Forge

**Observe. Correlate. Diagnose. Verify.**

Local-first API reliability, runtime intelligence and incident investigation —
a specialist in the FORGE ecosystem. It watches how running APIs evolve over
time, turns telemetry into evidence-backed investigations, and delegates
specialized diagnosis to the other Forges instead of re-implementing them.

> Status: **Cycle 1 — Core, Identity & Persistence** (see `docs/cycles/`).
> Portuguese README: [`README.pt-BR.md`](README.pt-BR.md).

## What it is (and is not)

| API Sentinel owns | Delegated to other Forges |
| --- | --- |
| API inventory & runtime identity | API engineering → `api-forge` |
| Telemetry ingestion & quality state | Contract/config diagnosis → `forge-doctor-api` |
| SLIs, SLOs, error budgets | Structural code graph → `code-atlas-forge` |
| Deterministic anomaly detection | Routing/governance → `the-forge` |
| Incident lifecycle & evidence | Agent sessions → `forge-agent-bridge` |
| Operations data for the Portal | Agentic tracing → `forge-lens` |

## Install

```bash
# from a checkout
./install.sh            # bash
install.ps1             # powershell
# or directly
pip install api-sentinel-forge
api-sentinel install
```

`api-sentinel install` is a guided setup (profile, workspace scope, Portal
integration when detected). Nothing external is required: the core runs
offline against local files.

## First investigation in three commands

```bash
api-sentinel doctor                 # environment health
api-sentinel status                 # workspace state
api-sentinel capabilities           # what this build actually offers
```

Import telemetry, analyze a service and open an incident — delivered in
Cycles 2–4 (see `factory/specs/inbox/`):

```bash
api-sentinel ingest ./telemetry.jsonl     # Cycle 2
api-sentinel analyze --service payments   # Cycle 3
api-sentinel incidents                    # Cycle 4
api-sentinel serve                        # local HTTP API for the Portal
```

## Layout

```
src/api_sentinel/   core package (domain / application / infrastructure / contracts / cli)
contracts/v1/       versioned JSON Schemas of the public envelopes
src/api_sentinel/migrations/  versioned SQLite migrations (packaged)
docs/               Cycle-0 canonical set + ADRs + cycle plans
factory/specs/      queued specs for Cycles 2–9 (loop-factory format)
tests/              unit / contract / integration / security / e2e / fixtures
lab/                sample API + reproducible scenarios (grows per cycle)
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | domain/user error |
| 2 | usage error |
| 3 | unexpected internal error |

## License

Apache-2.0 — see `LICENSE`.
