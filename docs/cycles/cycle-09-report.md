# Cycle 9 — Report: Production Hardening & Release Readiness

Cycle 9 proves, more than it builds. Everything below is a run record —
measured, not asserted. The two real defects the gates caught (a broken
wheel that installed a table-less database, and a CI configuration that
would have failed on Linux) are documented as findings, not hidden.

## Verification tails

### Windows (native, `DESKTOP-EDGAR`, Python 3.12.13)

```text
$ .venv/Scripts/python.exe -m pytest -q          # sockets: non-loopback blocked
440 passed in 33.10s

$ .venv/Scripts/python.exe -m pytest -m slow -q  # scale bench gate
2 passed, 438 deselected in 10.99s

$ .venv/Scripts/python.exe -m ruff check .
All checks passed!

$ .venv/Scripts/python.exe -m mypy src
Success: no issues found in 112 source files

$ uv build
Successfully built dist\api_sentinel_forge-0.1.0.tar.gz
Successfully built dist\api_sentinel_forge-0.1.0-py3-none-any.whl
```

### Linux (docker `python:3.12-slim`, repo-mounted, same `uv.lock`)

```text
$ uv sync --locked --all-extras --python 3.12 && uv run ruff check .
All checks passed!
$ uv run mypy src
Success: no issues found in 112 source files
$ uv run pytest -q
440 passed in 272.09s (0:04:32)
$ uv build
Successfully built dist/api_sentinel_forge-0.1.0.tar.gz
Successfully built dist/api_sentinel_forge-0.1.0-py3-none-any.whl
```

### CLI smokes (real binary, temp workspace)

```text
$ api-sentinel doctor --workspace <ws> --json
{"workspace_initialized": true, "database": "present",
 "sentinel": {"state": "available", "database": {"integrity": "ok",
  "size_bytes": 339968}, "tables": {"schema_migrations": 6, ...},
  "quarantine": {"depth": 0}, "sources": {...}, "unknowns":
  ["no_sources_registered"]}}

$ api-sentinel backup create --workspace <ws> --json
{"backup_dir": ".../backups/backup-20261109T014249Z",
 "manifest": {"schema": "api-sentinel/BackupManifest/v1",
  "app_version": "0.1.0", "at": "...", "tables": {...},
  "checksums": {"sentinel.db": "sha256:1ebd…", "config.yaml": "sha256:af7d…"}}}

$ api-sentinel backup restore --from <bk> --workspace <ws>
error[sentinel.conflict]: workspace has live data — pass --force
$ api-sentinel backup restore --from <bk> --workspace <ws> --force --json
{"replaced_paths": ["sentinel.db.prerestore-…"], "integrity": "ok"}

$ api-sentinel retention --workspace <ws> --json        # dry-run, 0 deletes
$ api-sentinel retention --workspace <ws> --apply --json
{"deleted": {...0s}, "receipt": ".../receipts/retention-….json"}
```

### Post-install wheel smoke (the gate that caught the packaging bug)

```text
$ uv venv smoke-venv && uv pip install dist/*.whl[api]
$ api-sentinel install -y --workspace smoke-ws --json   # → db: 23 tables
$ api-sentinel doctor --workspace smoke-ws --json
"workspace_initialized": true, sentinel.state = "available",
schema_migrations = 6
```

## Scale benchmark — measured, never extrapolated

`lab/bench/bench_ingest.py` feeds generated
`api-sentinel/RuntimeObservation/v1` JSONL through the real
`IngestionPipeline` (parse → normalize → dedup → persist), chunked at
5,000. Realistic producer shape: each file covers a contiguous time
slice. Raw artifacts: `factory/runs/cycle-09/bench-*.json`.

| N | elapsed | obs/s | tracemalloc peak | db size |
| --- | --- | --- | --- | --- |
| 10,000 | 8.07 s | 1,239.5 | 47.80 MiB | 8.87 MiB |
| 50,000 | 42.62 s | 1,173.2 | 47.82 MiB | 43.33 MiB |
| 100,000 | 98.22 s | 1,018.1 | 47.82 MiB | 86.37 MiB |

Findings, stated plainly:

- Steady-state ≈1,000–1,240 obs/s on this machine; **peak traced memory
  is flat at ~47.8 MiB regardless of N** — chunk-bounded, no growth.
- Storage ≈0.87 KiB/observation at this payload shape.
- **Measured worst-case path** (kept as `bench-*-worstcase.json`): a
  corpus where every chunk's windows span the whole 1440-window history
  degrades to ~930 obs/s at 10k and ~200 obs/s at 50k — each distinct
  `(service, window)` pair per batch costs one backwards range scan in
  `overlapping_sources`. Real producers emit time-ordered slices, so the
  reported numbers use that shape; the worst case is recorded as an
  operational characteristic, not patched over mid-cycle.
- No artificial threshold is asserted anywhere — the slow pytest asserts
  correctness only (all rows land, replay dedups, memory bounded by
  construction).

## Acceptance criteria

| # | AC | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Scale benchmark on target hardware, numbers under `factory/runs/`, no invented threshold | ✅ | `factory/runs/cycle-09/bench-{10000,50000,100000}.json` + table above |
| 2 | Retention (ADR-015) enforced + verified; perf within measured budgets | ✅ | `tests/integration/test_retention.py` (4 tests: dry-run, apply, idempotency, open-incident immunity) + CLI smoke |
| 3 | Documented backup/restore; LAB-014 consistent state/history | ✅ | `tests/integration/test_backup_restore.py` (4 tests incl. corrupt-backup refusal, no-force refusal, force moves-aside) + runbook §Backup |
| 4 | LAB-016 unauthorized principal denied without leakage | ✅ | `tests/integration/test_authz_matrix.py` (8 tests: 4 roles + intruder + empty + non-leakage + placeholder-read doc) — denials precede lookup, `api.scope.denied` audited |
| 5 | Each integration degraded independently; core stays up | ✅ | `tests/integration/test_degradation_matrix.py` (12 tests: Doctor/Atlas/Bridge/Lens/data-sentinel/The Forge + telemetry gap → `UNVERIFIED`, never fabricated health) |
| 6 | Self-observability: ingestion lag, quarantine size, storage health, connector states | ✅ | `sentinel_health()` → `doctor` + additive `sentinel` field in `/v1/health` (shape preserved); checkpoint freshness per source |
| 7 | Threat model re-evaluated; residual risks recorded | ✅ | `docs/07` "Cycle-9 delta" — serve/AWS/agent_sessions/ops-CLI surfaces + 4 recorded residuals |
| 8 | Multiplatform CI: Windows + Linux, packaging path | ✅ | `.github/workflows/ci.yml` (win+linux × py3.12, uv, ruff/mypy/pytest/build + wheel doctor gate); **executed** on Windows natively and Linux via container — see tails |
| 9 | RC process: tagged candidates + post-install doctor smoke | ✅ | `uv build` + fresh-venv `api-sentinel doctor` gate in CI and executed locally (caught the migrations packaging bug); tag `v0.1.0-rc.1` marks this commit |
| 10 | Docs/onboarding complete | ✅ | `docs/15` rewritten Backup/Retention/upgrade sections + CI mirror; install → ingest → incidents journey documented |
| 11 | Runbooks: backup/restore, connector failure, quarantine triage, budget exhaustion | ✅ | `docs/15` — all four sections |
| 12 | Safe upgrade/downgrade on populated workspaces; irreversibles documented | ✅ | forward-only migrations + checksum verified on populated ws (`test_storage.py`, LAB-014 restore round-trip); documented: no reverse migrations — backup restore IS the downgrade path |
| 13 | Contract compatibility matrix approved | ✅ | `tests/contract/test_schema_compat.py` (45 tests × 8 schemas: valid parse, unknown-version rejection, required-removal rejection, additive-optional tolerance, extension zones) |
| 14 | Offline suite with network blocked | ✅ | `tests/conftest.py` socket guard (non-loopback `connect`/`connect_ex`/`create_connection` refused; loopback allowed for asyncio/TestClient); 440 passed under it on both OSes |

## Constraint checks (§21.2)

| Constraint | Result |
| --- | --- |
| Deterministic-core branch coverage ≥90% | **90.50%** measured (`factory/runs/cycle-09/coverage.json`; lowest: `ingestion/importers.py` 73.9%, `application/ports.py` 75.3%) |
| Zero duplications on idempotent reprocessing | proven: bench replay test — 500 re-ingested → `accepted=0`, stored stays 500 |
| Zero known-secret leakage | unchanged redaction + quarantine paths; no new payload capture added this cycle |
| Zero silent evidence loss | retention protects referenced evidence *by reference*, proven with active-incident/anomaly/orphan cases; orphan blob files reported, never dropped |
| Unknown contract version never silently accepted | `parse_envelope` + typed-model rejection tested for every v1 schema |
| Audit events append-only | retention never touches `audit_events` unless `--audit-days` is explicit; operations themselves journal `operations.*` rows |
| Hardening only — no new subsystems/contracts | only additive surface: `sentinel` dict in `/v1/health` (additive field, shape preserved); `operations/` package is ops tooling, not domain; one packaging fix (migrations moved into the wheel — structural, not a feature) |

## Files

- **Operations package:** `src/api_sentinel/operations/{__init__,backup,retention,health}.py`
- **Surface:** `src/api_sentinel/cli/__init__.py` (`backup create/restore`, `retention`, `doctor` sentinel block), `api/routers/health.py` + `api/schemas.py` (additive `sentinel`/`unknowns`), `cli/install.py` unchanged
- **Packaging fix:** `migrations/` → `src/api_sentinel/migrations/` (wheel now carries SQL); `storage/migrations.py` (packaged path + loud failure on zero discovered files); `pyproject.toml` (sdist list, `[[tool.mypy.overrides]]` for boto3/botocore); `integrations/aws/client.py` (portable import)
- **Bench:** `lab/bench/bench_ingest.py`, `factory/runs/cycle-09/*.json`
- **Tests:** `tests/conftest.py` (socket guard), `tests/contract/test_schema_compat.py`, `tests/integration/{test_backup_restore,test_authz_matrix,test_degradation_matrix,test_retention,test_bench_scale}.py`, import-sort fixes in 4 pre-existing test files (Linux ruff parity)
- **CI:** `.github/workflows/ci.yml`
- **Docs:** `docs/07` Cycle-9 delta, `docs/15` backup/retention/runbook/upgrade/CI sections, `README.md` layout, `CHANGELOG.md` `[0.1.0-rc]`, this report

## Real defects found by the gates (and fixed)

1. **Wheel installed a table-less database.** `MIGRATIONS_DIR` resolved
   `migrations/` at repo root — sdist-only, absent in the wheel — and
   `migrate()` silently applied zero files. `install` then produced
   `sentinel.db` with no tables; `doctor` crashed on `SELECT` instead of
   degrading. Caught by the post-install smoke gate; fixed by moving the
   canonical SQL into the package and making `migrate()` fail loudly on
   an empty discovery set.
2. **CI would have failed on Linux.** `uv sync --locked` (dev group)
   leaves `botocore` absent → `mypy src` fails; and four pre-existing
   test files had import groupings Linux ruff rejects (`conftest`
   first-party). Fixed via `--all-extras`, the mypy override, and the
   import-sort normalization — both legs now execute the identical gate.
3. **Bench initially measured a worst case, not throughput.** The
   generator spread every chunk across 1440 windows, turning the
   per-batch overlap check into the dominant cost (~200 obs/s). The
   workload now reflects real producer shape; the worst case is
   preserved as a documented characteristic.

## Non-executable homologation items (unticked, by design)

- **Real AWS account** end-to-end collect — no credentials in dev;
  checklist remains in `cycle-07-report.md`.
- **Live Bridge/Doctor/Lens peers** consuming handoff envelopes — peers
  are out-of-repo; handoff-file transport is verified, peer consumption
  is not executable locally.
- **Portal UI** — lives in the Portal repo; `/v1` API surface is what
  this cycle hardened.
- **GitHub-Actions CI execution** — the workflow is defined and both
  legs were executed locally (native + container); the first
  *platform-hosted* run happens on push.
- **Binary-version downgrade** — intentionally unsupported (no reverse
  migrations); the verified rollback is backup→restore.

Commit: see `git log -1` (`Cycle 9: production hardening,
backup/restore, retention, CI`). Tag: `v0.1.0-rc.1`.
