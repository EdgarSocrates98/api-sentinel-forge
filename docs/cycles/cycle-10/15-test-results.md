---
cycle: 10
deliverable: Test results — Wave 2 verification gate
wave: 2
---

# Cycle 10 — Test results (Wave 2)

## Gates (all green)

| Gate | Result |
|---|---|
| `pytest -q` (full suite) | **538 passed** in ~48–58s |
| `ruff check .` | clean — 0 errors (was 13: fixed tests+`cov_core.py`(F013)+lab fixture scope) |
| `mypy src` (strict) | clean — 116 source files |

## Wave-2 batteries

| Battery | Count | Covers |
|---|---|---|
| `tests/integration/test_doctor_negative.py` | 14 | absent module · version mismatch · invalid OpenAPI · truncated handoff · timeout · nonzero exit · malformed bundle · unresolved `$ref` · path escape · missing required field (F014) · wrong schema literal · delegated≠executed · envelope shape |
| `tests/unit/test_integrations_atlas_store.py` | 10 | missing/malformed db · ambiguous repo (no selector vs explicit) · no READY snapshot · unknown snapshot_id · unresolved identity · external/unresolved edges · observed-vs-structural separation |
| `tests/unit/test_integrations_boundary.py` | 11 | all 8 execute pairs produce conformant `theforge/ExecutionResult/v1` · named refusals for unknown pairs · manifest declares only executor-backed pairs (F015) · scope/ambiguity rules (F017) |
| `tests/security/test_handoff_security.py` | updated | `payload`/`constraints` required (F014) · redaction/bounds preserved |

## Real-runtime proofs (not test doubles)

- `theforge ask` routed runs — `evidence/theforge/runs/*/verification.json`
  `status=passed` for each delivered result.
- Real Doctor in-process run — `evidence/doctor_inprocess_run.json`.
- Real Atlas index + correlate — `evidence/atlas/` +
  `evidence/atlas_correlate_run.json`.
- `theforge provider check` — protocol gates green; execute/handoff probes
  on the conformance scratch workspace return honest
  `APISENT-WORKSPACE-UNAVAILABLE` (C10-F016, recorded — not a product bug).
