# Cycle 10.1 — Release Closure Report

Scope: `prompt_evo_cycle10.1.md` — remediation + release closure for
`Observe → Detect → Correlate → Diagnose → Route → Investigate → Verify →
Record` across the Forge ecosystem.

Branch: `fix/cycle-10.1-release-closure` (api-sentinel-forge).
Sibling branch: `fix/cycle-10.1-apisentinel-adapter-py311` (the-forge,
worktree `the-forge-adapter-fix`).

Governing rule applied throughout: unavailable optional integrations are
*named states*, never simulated; every completed stage carries evidence
that distinguishes local-real, integration-real, fixture, simulated,
blocked and not-run.

## Finding dispositions

| ID | Finding | Disposition | Evidence |
|----|---------|-------------|----------|
| C101-F001 | Doctor delegation timeout ineffective — `ThreadPoolExecutor.__exit__` waits for the blocked worker | **FIXED** — delegation runs in a dedicated child process with hard wall-clock timeout, terminate→grace→kill escalation, `OSError`-tolerant cleanup (child exit races), env allowlist, bounded stdout/stderr files, redacted stderr, named worker exit codes, redacted receipt per attempt | `src/api_sentinel/integrations/doctor/{adapter,_worker}.py`; `tests/integration/test_doctor_process.py` (26 tests: blocked child, ignores-terminate, next-run-after-timeout, concurrent isolation, crash, unavailable, malformed/oversized output, secret redaction) |
| C101-F002 | S18 receipt claimed S1–S18 but contained 17 steps (S18 built the snapshot before appending itself); no independent verifier | **FIXED** — receipt built from `steps_all = ORACLE + [S18]`, `steps/S18.json` written, sha256 manifest over final evidence bytes, separate `lab/verify_evidence.py` checks every hash+size, receipt↔step equality, step↔manifest coverage, summary counters, root-escape paths | `lab/federated_e2e.py:s18_receipt`; `lab/verify_evidence.py`; `tests/integration/test_evidence_integrity.py` (clean pass + tamper→named `hash_mismatch`/`size_mismatch`/`receipt_step_mismatch`) |
| C101-F003 | the-forge api-sentinel adapter declared `requires-python = ">=3.10"` while importing `api_sentinel` (needs `>=3.11`) — installable into an env where it cannot run | **FIXED** (the-forge repo) — bound raised to `>=3.11` | the-forge worktree commit `4e220d3` on `fix/cycle-10.1-apisentinel-adapter-py311` |
| C101-F004 | Bridge "delegation" was a handoff-file drop — no governed transport | **FIXED** — `HttpAgentSession` posts the governed request to `POST /agent-sessions` (bearer, `X-FAB-Nonce`, loopback `Origin`), loopback-only URLs enforced, pair-token exchange when no bearer configured (`FAB_BEARER` → configured → `~/.forge-bridge/pair.token`), named outcomes (2xx→delegated, auth/validation/conflict/429→refused, unreachable→unavailable, timeout/5xx/transport→failed). Provider execution stays Bridge-owned; `FileAgentSession`/`UnavailableAgentSession` preserved | `src/api_sentinel/integrations/bridge/adapter.py`; `tests/integration/test_bridge_http.py` (9 tests incl. live `forge-bridge.exe` contract + pair exchange) |
| C101-F005 | Lens trace context had no canonical export path; no Incident→Session→Trace chain evidence | **FIXED** — `lab/lens_export_refs.py` runs real `forge-lens traces list --json` and emits the refs manifest `FileLensACL` consumes (allowlist only — trace payloads stay Lens-owned); real-chain test proves bridge session id correlates to a stored trace | `tests/integration/test_lens_real.py` (2 tests, real `forge-lens.exe` init→ingest→list→ACL) |
| C101-F006 | (subsumed under F007) | — | — |
| C101-F007 | Ingest throughput regression suspected vs cycle-09/10 | **DISPROVEN** — rerun matrix: sequential 1408 obs/s, dispersed 1503, out_of_order 1532, multi_source 1509, high_cardinality 1480, dup_heavy 936, mid_batch_failure 1445, concurrent 1528 — all ≥ cycle-10 baseline (1075.6 / 934.9); tracemalloc peaks 42–84 MiB | `lab/run/bench-cycle101.json` |
| C101-F008 | Federated CI gates | Local gates re-run (see Gates); hosted CI observed on the PR | this report §Gates |
| C101-F009 | Simulated telemetry could produce an unlabeled healthy SLO conclusion | **FIXED** — `evaluate_slo(source_kinds=…)` stamps `source_kind` per `SourceAssessment`; synthetic kinds (`simulated|fixture|synthetic|demo`) add `simulated_source_data:<ids>` and `simulated_primary_source` limitations. Math unchanged — provenance labeled, never promoted | `src/api_sentinel/reliability/slo.py`; `tests/unit/test_reliability.py` (2 oracles) |
| C101-F010 | Packaging/install validation | **VERIFIED** — `uv build` wheel+sdist; wheel installed into a clean venv; `api-sentinel --help` lists full command surface; `api-sentinel doctor` reports honest `unavailable`/unknowns on an uninitialized workspace | `dist/api_sentinel_forge-0.1.0-*.whl`; smoke transcript (this report) |
| C101-F011 | forge/v1 adapter conformance | **VERIFIED** — live probe through the real stdio seam covers ops + negative cases | `docs/cycles/cycle-10.1/evidence/theforge-conformance.json` |
| C101-F012 | Boundary `forge/v1` parameter handling | **VERIFIED** — contract-faithful; no drift found | same probe |

## Gates

| Gate | Result |
|------|--------|
| `pytest -q` (full) | see `lab/run/pytest-full.log` — recorded below |
| `ruff check` changed files | PASS |
| `mypy` changed files | PASS |
| `uv build` | PASS — wheel + sdist |
| Wheel install + `doctor` | PASS — honest unavailable states |
| Bench matrix | PASS — no regression vs cycle-10 |
| Federated E2E S1–S18 | see `lab/run/e2e-cycle101.log` + receipt/manifest |

## Truth table

- **local-real**: pytest suite, ruff, mypy, build, wheel install, bench.
- **integration-real**: Doctor child-process worker; Bridge live service
  (`forge-bridge.exe`) including pair exchange; Lens live CLI
  (`forge-lens.exe`) init→ingest→list→ACL; the-forge adapter conformance
  probe.
- **fixture**: `native-ingest.json` (Lens fixture), demo traffic generator.
- **simulated**: demo workload traffic — now *labeled* in SLO assessments.
- **unavailable→named**: Bridge/Lens/Doctor absent → `unavailable` states,
  never fabricated.
