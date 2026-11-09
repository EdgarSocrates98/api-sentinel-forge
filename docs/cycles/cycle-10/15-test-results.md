---
cycle: 10
deliverable: Test results — per-wave verification gates, final re-run
wave: 5 (consolidated)
---

# Cycle 10 — Test results

## Final gate (Wave 5, HEAD)

| Gate | Result |
|---|---|
| `pytest -q` (full suite) | **544 passed** in ~44 s |
| `ruff check .` | clean — 0 errors |
| `mypy src` (strict) | clean — 116 source files |
| `uv build` | clean — wheel (127 files) + sdist (190 files) |
| fresh-venv smoke | wheel `[api]` → install/doctor/capabilities/mcp — `evidence/installation-results.json` |

Suite growth across the cycle: 440 (Wave 0 baseline) → 514 (Wave 1) →
538 (Waves 2–3) → **544** (Waves 4–5: ingest-batching regression tests,
bundle-version gate, demo-exporter contract fix).

## Wave gates at execution time

| Wave | pytest | Notes |
|---|---|---|
| 0 baseline | 440 passed / 58.45 s | `evidence/baseline-tests.txt` verbatim |
| 1 hardening | 514 passed | auth battery, handoff, storage, contract negatives, MCP tools |
| 2 federation | 538 passed | +doctor negatives (14), atlas-store (10), boundary executors (11) |
| 3 portal UX | 538 passed | sentinel-side unchanged; portal suite 624/57 files (npm test) |
| 4 e2e/perf | 544 passed | +ingest batching (5), translate bundle gate (F022), demo exporter (F023) |
| 5 release | **544 passed** | capabilities stage refresh (portal-operations/aws → verified) |

## Wave-2+ batteries (all inside `pytest -q`)

| Battery | Count | Covers |
|---|---|---|
| `tests/integration/test_doctor_negative.py` | 14 | absent module · version mismatch · invalid OpenAPI · truncated handoff · timeout · nonzero exit · malformed bundle · unresolved `$ref` · path escape · missing required (F014) · wrong schema literal · delegated≠executed · envelope shape |
| `tests/unit/test_integrations_atlas_store.py` | 10 | missing/malformed db · ambiguous repo · no READY snapshot · unknown snapshot_id · unresolved identity · external/unresolved edges · observed-vs-structural separation |
| `tests/unit/test_integrations_boundary.py` | 11 | 8 execute pairs → conformant `theforge/ExecutionResult/v1` · unknown-pair refusals · manifest honesty (F015) · scope/ambiguity (F017) |
| `tests/contract/test_theforge_adapter.py` | 5 | catalog snapshot + live-catalog agreement + boundary id parity |
| `tests/security/test_api_auth.py` + `test_handoff_security.py` + `test_storage_recovery.py` | 28 + 22 + 12 | bearer/HMAC auth, scope matrix, replay, bounded reads, path guards, backup/restore corruption paths |
| `tests/integration/test_ingest_batching.py` | 5 | batch dedup/overlap/prefetch, replay equivalence, quarantine parity (F006) |
| `tests/integration/test_mcp_server.py` | 4 | read-only MCP surface, honest errors |

## Real-runtime proofs (not test doubles)

- `theforge ask` routed runs — `evidence/theforge/runs/*/verification.json`
  `status=passed` for each delivered result.
- Real Doctor in-process run — `evidence/doctor_inprocess_run.json`.
- Real Atlas index + correlate — `evidence/atlas/` +
  `evidence/atlas_correlate_run.json`.
- Real Portal journey — `evidence/portal-verification.json` (19/19).
- Federated golden path — `evidence/federated-e2e-receipt.json`
  (16 pass / 2 BLOCKED / 0 fail).
- §12 security battery — `evidence/security-results.json` (40/40).
- `theforge provider check` — protocol gates green; execute/handoff
  probes on the conformance scratch workspace return honest
  `APISENT-WORKSPACE-UNAVAILABLE` (C10-F016, recorded — not a product bug).

## Named not-run cells

- Hosted CI (GHA) — runs on push/PR; local gates mirror it 1:1.
- Interactive TTY install wizard — see `14-packaging-installation.md` §3.
- Forge-portal browser suite runs in the portal repo (Playwright), not
  in this pytest suite — evidence linked above.
