---
id: cycle-10-wave4-e2e-perf
title: "Cycle 10 Wave 4 — Federated E2E golden path + performance closure"
agent: claude
risk: high
verification:
  - "python -m pytest -q"
  - "python lab/federated_e2e.py"
---

# Context

Implements prompt_evo_cycle10.md §8 (Phase 4 golden path S1–S18), §10 (Phase 5 performance), §11 (Phase 6 AWS), §12 (advanced security battery). Runs on `cycle-10`. INPUTS: waves 0–3 outputs, `lab/`, `lab/bench/bench_ingest.py`, Wave-0 F006 perf finding.

# Acceptance Criteria

- [x] **Real lab app:** `lab/demo_api/` — a small FastAPI (or stdlib) HTTP service with OpenAPI, instrumented endpoints (measurable latency, a controllable local dependency, reproducible 503 mode, version-tagged deploys, sanitized logs, exportable metrics) — real process, real requests, cleanup verified.
- [x] **Golden path runner** `lab/federated_e2e.py` executing S1–S18 with per-step oracle records {id, command, input, output, process, ts, status, commit, version, provider, workspace, artifacts, hashes, errors, limitations} and truth labels LOCAL_REAL / INTEGRATION_REAL / FIXTURE / SIMULATED / EXTERNAL_REAL / NOT_RUN / BLOCKED — no indiscriminate "REAL".
- [x] S1 bootstrap from a CLEAN WHEEL install (fresh venv, `uv build` artifact, no PYTHONPATH/src).
- [x] S9 real Doctor + S10 real Atlas + S11 the-forge routing reused from Wave 2 wiring (skipped w/ BLOCKED only if Wave 2 honestly failed — record why).
- [x] S12/S13 Bridge/Lens: real if provider exists, else BLOCKED with exact operator instructions — never fake.
- [x] S14 Portal step uses the real served Portal (if Wave 3 landed) hitting Sentinel backend, else NOT_RUN documented.
- [x] S15 recovery: remove degradation → new observations → indicators recover.
- [x] S17 negative security battery per §12: API (bypass/spoof/CORS/workspace isolation/BOLA/replay/oversized/rate), storage (traversal/symlink/corrupt/malformed quarantine), agentic (prompt injection, tool-output injection, unauthorized pack, tampered handoff, session replay), cloud (credential leak, mutating-API attempt, SSRF, endpoint override, cross-account) — lab-controlled, results in `evidence/security-results.json`.
- [x] **Performance closure:** reproduce F006 baseline (sequential + dispersed windows + out-of-order + multi-source + high-cardinality + dup-heavy + reprocess + large file + mid-batch failure); profile the overlap-source check (query counts/plans); implement the minimal root-cause fix (index/batch/cache — no correctness sacrifice); rerun: `evidence/performance-before.json` + `performance-after.json` with real numbers and per-stage timings; regression suite green.
- [x] **AWS per §11 — REAL account authorized:** account `7025-6177-1161` (name `XedX-Teste`, free tier) authorized by the owner. Local AWS CLI v2.36.38 present with profile `sparkforge` (region `us-east-1`, `login_session` for that account, session may need `aws login --profile sparkforge` reauth — owner performs it). REAL path when session is live: (a) `aws sts get-caller-identity --profile sparkforge` MUST return account `702561771161` — any other account → abort and mark BLOCKED wrong-account; (b) register a `cloudwatch_metrics` source + `apigateway` discovery source configured with profile/region, run REAL bounded queries: API Gateway REST+HTTP discovery (`GetRestApis`/`GetApis`), CloudWatch `GetMetricData` for API GW metrics (`Count`, `5XXError`, `Latency`, `4XXError`) and/or free-tier-safe namespaces present in the account (AWS/EC2 status checks, AWS/Lambda, AWS/S3 daily storage — whatever resources actually exist), ingest into Sentinel, produce SLO/anomaly evidence FROM REAL AWS DATA labeled `EXTERNAL_REAL`; (c) receipts: STS identity recorded (account/arn redacted appropriately), every call logged with op+params+duration, zero credentials persisted anywhere; (d) budgets enforced — free tier: bound to small windows (≤1h ranges, ≤10 metrics), abort on budget exceeded; (e) negative battery still runs against simulators (expired creds, wrong account/region, AccessDenied, throttling, partial, pagination, timeout, missing metric, invalid dimension, budget, network fail, empty, mutating-API refused). If session NOT live at run time → record BLOCKED with exact checklist (`aws login --profile sparkforge` + rerun command).
- [x] `docs/cycles/cycle-10/{11-federated-e2e,12-performance-report,13-aws-readonly-validation}.md` + `evidence/{federated-e2e-receipt,performance-before,performance-after,security-results}.json` + `evidence-manifest.json` seeded.

# Constraints

- Evidence-first: every number real or labeled estimate; no mock upgraded to REAL status.
- Perf fix must preserve idempotency/dedup correctness — replay-equivalence tests mandatory.
- No mutation of cloud or sibling repos this wave (sibling work finished in Waves 2–3).
- Keep bench runs bounded (< ~10 min each on this machine).

# Review Notes

- Check truth labels are honest (handoff file ≠ consumed; local API ≠ AWS prod).
- Check before/after perf uses identical datasets.
- Check security battery failures were actually attempted, not assumed.

# Grill Gate

- Owner: Edgar
- Out of scope: real agent-provider session (Bridge/Lens), Portal-hosted CI, mutating AWS APIs. AWS real account IS in scope (7025-6177-1161 free tier, profile `sparkforge`) contingent on live login session at run time.
- Riskiest assumption: the perf root cause is the suspected overlap-source N+1 (prove or refute first).
- Smallest acceptable version: golden path runs end-to-end locally + perf before/after + security battery + BLOCKED records where external.
- _Depends: waves 1–3_
