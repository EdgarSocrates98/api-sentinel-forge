---
cycle: 10
deliverable: federated-e2e
wave: 4
---

# Cycle 10 — Federated E2E Golden Path (S1–S18)

**Wave 4. Verdict: PASS — 16 pass / 2 BLOCKED / 0 fail.**
`lab/federated_e2e.py` drives the full S1–S18 golden path against real
processes, real installs, and real cross-Forge integrations. Every step
writes an oracle record `{id, command, input, output, process, ts,
status, commit, version, provider, workspace, artifacts, hashes,
errors, limitations}` plus a truth label — never an indiscriminate
"REAL".

Evidence: `evidence/federated-e2e-receipt.json` (all 17 oracle records,
step S18 written after the receipt snapshot), per-step records in
`lab/run/fed/steps/S*.json`, run artifacts under `lab/run/fed/`.

## Step results

| step | truth | verdict | what it proved |
|---|---|---|---|
| S1 | LOCAL_REAL | pass | clean-wheel bootstrap: `uv build` wheel → fresh venv install (`boot-venv`), `api-sentinel.exe` runs with no `src/` on sys.path; `forge_doctor_api` importable in-process |
| S2 | LOCAL_REAL | pass | real processes: stdlib dependency (8330) + FastAPI demo (8320) started, `/healthz` + OpenAPI up |
| S3 | LOCAL_REAL | pass | workspace `install`, service `svc_01M70EJMY02EXJC4WFEWQPRJX2` + jsonl-file source registered; Atlas identity `service.app#app` declared |
| S4 | LOCAL_REAL | pass | 783 real HTTP requests, 100 % 200, real measured latencies |
| S5 | LOCAL_REAL | pass | `/metrics/export` → `RuntimeObservation/v1` → real ingest accepted (closed 60 s windows only) |
| S6 | LOCAL_REAL | pass | SLO defined + evaluated: `MET`, SLI 1.0 (eligible 786) |
| S7 | LOCAL_REAL | pass | real degradation: dependency → `fail` mode → 789 requests, **100 % 503** |
| S8 | LOCAL_REAL | pass | `error_ratio_over_limit` fired (5 other rules clean); incident `inc_a80b8c99196ed31023977275` created; investigation delegated through the real Agent Bridge boundary (session request `asr_102c86f2…`) |
| S9 | INTEGRATION_REAL | pass | real Doctor in-process delegation → `completed` via `DoctorBoundary` |
| S10 | INTEGRATION_REAL | pass | real Atlas structural correlate: `OBSERVED`, 3 declared `CONTAINS` edges `service.app#app` → `GET /orders`, `POST /orders`, `GET /orders/{order_id}` |
| S11 | INTEGRATION_REAL | pass | real the-forge routing: `theforge ask run api.anomaly-detection` → provider `api-sentinel-forge` executed (`run-det_3858c1ba…`, 6 evaluations, duration_ms measured) |
| S12 | BLOCKED | blocked | Agent Bridge — no transport provider on this host; session request written, named unavailable, exact operator instructions recorded |
| S13 | BLOCKED | blocked | Lens — no forge-lens provider/refs manifest configured; named unavailable with the export instructions |
| S14 | INTEGRATION_REAL | pass | real served Portal bundle → same-origin `/sentinel` proxy → real Sentinel `/v1/services` (demo service returned) → Playwright browser: connection card, services render, incidents render, token never in DOM, 2 screenshots |
| S15 | LOCAL_REAL | pass | recovery: dependency → `ok` + deploy `v1.1.0` recorded as change `chg_b828f2e5…` → 791 requests ~100 % 200 → recovery-window SLO `MET` 1.0, **0 new error anomalies** |
| S16 | LOCAL_REAL | pass | `backup create` → `backup restore` into fresh root → integrity `ok` + row-count parity |
| S17 | LOCAL_REAL | pass | §12 security battery: **40/40 controls held** (`evidence/security-results.json`) |
| S18 | LOCAL_REAL | pass | consolidated receipt + evidence manifest |

## Honest-state notes

- **S12/S13 are blocked, not failed, and not skipped.** Bridge has no
  transport provider and Lens has no refs manifest on this host; the
  runner records the named-unavailable state plus the exact operator
  instructions to activate them.
- **S10 correlation is structural only** — declared `CONTAINS` edges
  with `provenance: declared`; observed/structural edges stay distinct,
  temporal proximity is never represented as causality.
- **S11** executed the real provider subprocess through the-forge's
  deterministic routing; the run artifact lives under `.forge/runs`.
- The demo dependency's `fail` mode is a real TCP-level gate inside
  `/work` — the 789×503 stream is the app's own responses, not a stub.
- Window-close waits (up to ~60 s) are inherent to closed-window
  metrics; S4/S7/S15 each drive ≥1 closed window before export.

## Performance / security / AWS companions

- F006 N+1 closure: `12-performance-report.md` +
  `evidence/performance-{before,after}.json` (all 10 shapes, parity).
- §12 battery: `evidence/security-results.json` — 40/40 held
  (1 real boundary hardened: C10-F022).
- §11 AWS: `13-aws-readonly-validation.md` + `evidence/aws-validation.json`
  — real bounded collect on account `7025****1161`, receipts masked.
