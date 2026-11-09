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

- [ ] **Real lab app:** `lab/demo_api/` — a small FastAPI (or stdlib) HTTP service with OpenAPI, instrumented endpoints (measurable latency, a controllable local dependency, reproducible 503 mode, version-tagged deploys, sanitized logs, exportable metrics) — real process, real requests, cleanup verified.
- [ ] **Golden path runner** `lab/federated_e2e.py` executing S1–S18 with per-step oracle records {id, command, input, output, process, ts, status, commit, version, provider, workspace, artifacts, hashes, errors, limitations} and truth labels LOCAL_REAL / INTEGRATION_REAL / FIXTURE / SIMULATED / EXTERNAL_REAL / NOT_RUN / BLOCKED — no indiscriminate "REAL".
- [ ] S1 bootstrap from a CLEAN WHEEL install (fresh venv, `uv build` artifact, no PYTHONPATH/src).
- [ ] S9 real Doctor + S10 real Atlas + S11 the-forge routing reused from Wave 2 wiring (skipped w/ BLOCKED only if Wave 2 honestly failed — record why).
- [ ] S12/S13 Bridge/Lens: real if provider exists, else BLOCKED with exact operator instructions — never fake.
- [ ] S14 Portal step uses the real served Portal (if Wave 3 landed) hitting Sentinel backend, else NOT_RUN documented.
- [ ] S15 recovery: remove degradation → new observations → indicators recover.
- [ ] S17 negative security battery per §12: API (bypass/spoof/CORS/workspace isolation/BOLA/replay/oversized/rate), storage (traversal/symlink/corrupt/malformed quarantine), agentic (prompt injection, tool-output injection, unauthorized pack, tampered handoff, session replay), cloud (credential leak, mutating-API attempt, SSRF, endpoint override, cross-account) — lab-controlled, results in `evidence/security-results.json`.
- [ ] **Performance closure:** reproduce F006 baseline (sequential + dispersed windows + out-of-order + multi-source + high-cardinality + dup-heavy + reprocess + large file + mid-batch failure); profile the overlap-source check (query counts/plans); implement the minimal root-cause fix (index/batch/cache — no correctness sacrifice); rerun: `evidence/performance-before.json` + `performance-after.json` with real numbers and per-stage timings; regression suite green.
- [ ] **AWS per §11:** if no authorized profile/creds exist → mark `BLOCKED` with the exact homologation checklist; run the full simulated negative battery (no creds, expired, wrong account/region, AccessDenied, throttling, partial, pagination, timeout, missing metric, invalid dimension, budget exceeded, network fail, empty result, mutating attempt refused).
- [ ] `docs/cycles/cycle-10/{11-federated-e2e,12-performance-report,13-aws-readonly-validation}.md` + `evidence/{federated-e2e-receipt,performance-before,performance-after,security-results}.json` + `evidence-manifest.json` seeded.

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
- Out of scope: real AWS account, real agent-provider session, Portal-hosted CI.
- Riskiest assumption: the perf root cause is the suspected overlap-source N+1 (prove or refute first).
- Smallest acceptable version: golden path runs end-to-end locally + perf before/after + security battery + BLOCKED records where external.
- _Depends: waves 1–3_
