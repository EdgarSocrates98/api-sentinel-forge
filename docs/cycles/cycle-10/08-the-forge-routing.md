---
cycle: 10
deliverable: The Forge routing — real registration, routing, execution, receipts (F010 closed)
wave: 2
---

# Cycle 10 — The Forge routing (Wave 2: REAL)

## What changed in Wave 2

- **C10-F010 FIXED.** `SentinelBoundary` advertised 7 capability/action
  pairs but `_EXECUTORS` mapped only 2 — the rest died with
  `APISENT-NO-EXECUTOR`. All **8** declared pairs now have real executors
  wired to the existing use cases (`incident-investigation` gained the
  catalog-declared `show` action):

| Pair | Executor → use case |
|---|---|
| `api.service-inventory/list` | `ListServices` |
| `api.runtime-observation/summarize` | observation repository aggregate (all services when unscoped) |
| `api.slo-assessment/evaluate` | `EvaluateSlo` (explicit id or single-definition scope) |
| `api.anomaly-detection/run` | `RunDetection` (replay-dedup: second run reports `no new anomalies`) |
| `api.incident-investigation/list` | `ListIncidents` |
| `api.incident-investigation/show` | `ShowIncident` (+ journal evidence) |
| `api.deployment-correlation/correlate` | `Correlate` (observed + structural, never merged) |
| `api.doctor-handoff/delegate` | `DelegateToDoctor` |

## Adapter + registration (real, not simulated)

- `adapters/apisentinel/` — stdlib-only `forge/v1` provider
  (`theforge-apisentinel-adapter==0.1.0`), installed into the Sentinel venv;
  subprocess JSON bridge → `SentinelBoundary.handle()`. Recorded native
  surface fingerprint `e5ed6e175d00`, catalog verification `5/5 supported`.
- Copied to `the-forge` on branch **`cycle-10-apisentinel`**, commit
  `5aea039` (+ mypy path/ignore_missing_imports per the adapters/
  convention). Provider registered in the lab workspace
  (`E:\temp\forge-lab\.forge`) and trusted via user-level config — project
  `providers.toml` entries remain `untrusted` by the-forge design
  (unverified trust is never granted automatically).
- `theforge registry refresh` → `state=ready`, 7 capabilities — after
  C10-F015 fixed the manifest emitting `default_action: null` for
  CLI-scoped capabilities (now only executor-backed pairs are declared).

## Routing transcript — discover → route → execute → validate → receipt

Real `theforge ask` runs against `E:\temp\forge-lab`, each with
`task.json → routing.json → result.json → verification.json → receipt.json`
persisted by the-forge and copied to
`evidence/theforge/runs/<run_id>/`:

| Run id | Pair | Outcome |
|---|---|---|
| `20261109T131310Z-34446ffc` | anomaly-detection/run | ok — findings after FORGE-RESULT-DANGLING-EVIDENCE fix (C10-F018) |
| `20261109T131313Z-21329c86` | incident-investigation/list | ok — persisted incidents listed |
| `20261109T131316Z-e7974201` | doctor-handoff/delegate | ok — real Doctor handoff delegated |
| `20261109T131442Z-0e57520c` | slo-assessment/evaluate (unpinned) | ok — single-definition scope resolved |
| `20261109T132216Z-004a614a` | deployment-correlation/correlate | ok — Atlas-backed structural edges, `correlation is not causation` limitation |

Plus earlier refusals/validation captures under `evidence/theforge/`
(`ask_*.json`, `registry_refresh.json`, `registry_show.json`,
`providers_health.json`, `conformance_check.json`).

## Honesty rules on the wire (C10-F016/F017)

- `theforge provider check` probes every pair against an empty scratch
  workspace — a workspace-backed provider honestly returns
  `APISENT-WORKSPACE-UNAVAILABLE` there rather than fabricating or
  auto-creating state. Protocol gates all green; execute/handoff probes
  "fail" by harness design for stateful providers (recorded, peer-side
  recommendation deferred).
- `forge/v1` has no parameter channel on plain `ask` — scope resolves
  deterministically: explicit param → single-service workspace → **named
  ambiguity** (`service_id required: N services registered — never
  guessed`). Zero services → `APISENT-MISSING-PARAM`. No name matching, no
  intent-string parsing.
- Severity mapping is mechanical: `SEV1→critical · SEV2→high · SEV3→medium
  · SEV4→low · INFORMATIONAL→info`.
- Every finding's `evidence_ids` entry is re-emitted as a declared
  `Evidence` record (epistemic=`observed`, producer=`api-sentinel-forge`) —
  sentinel-side refs (`svc-row:`, `slo-assessment:`, handoff paths) ride
  the wire verbatim as ids.
