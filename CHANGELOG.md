# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses Semantic Versioning.

## [Unreleased]

### Fixed — packaging & bootstrap honesty (Cycle 16.1.1 wave 02)

- `setup.sh`: a missing Python no longer kills the script silently —
  `PY=$(command -v …)` under `set -e` exited `1` with no output before
  reaching the `FORGE-PYTHON-NOT-FOUND` branch; the probe now tolerates
  the all-miss case so the named error prints and exits `2`, matching
  `setup.ps1`. Pinned by `tests/unit/test_setup_scripts.py` running the
  real script on a python-free PATH.
- `aws collect` (and `aws discover` via `real_clientset`): an AWS
  credential provider requiring an optional transport (e.g. the `aws
  login` provider's `botocore[crt]`) raised `MissingDependencyException`
  at client construction — outside `resolve_session`'s error mapping —
  surfacing as an ungoverned crash. Client construction now maps to the
  governed `aws_unavailable` error with the missing-extra remedy.
- `pyproject.toml`: removed a stale ruff per-file-ignore for
  `scripts/forge_installkit.py` — the file does not exist; the vendored
  installkit lives at `src/api_sentinel/_installkit.py` (byte-parity
  `63bc9936eec43957`, shipped in the wheel).

### Verified — distribution proof (Cycle 16.1.1 wave 02)

- `uv build` sdist+wheel inspected: `api_sentinel/_installkit.py`,
  `_forgecontext.py`, `__main__.py`, `cli/` and `contracts/` all ship.
- Wheel installed into a disposable venv (cwd outside the checkout,
  `PYTHONPATH` clean): `api-sentinel --help`, `api-sentinel install
  --help` and `python -m api_sentinel --help` all green; `mcp serve`
  without the extra refuses honestly (`error[sentinel.validation]`,
  rc=1); with `api-sentinel-forge[mcp]` a real JSON-RPC
  `initialize`/`tools/list` handshake returns 4 tools.
- Windows/Git-Bash `setup.sh` path: logic-reviewed, not executed here —
  `UNVERIFIED` on this platform.

### Added — install lifecycle evidence (Cycle 16.1 wave 12)

- `forge/InstallLifecycle/v1` block on every emitted receipt and health
  doc (`install`, `install status|doctor|mcp-verify`, `repair`,
  `uninstall`): a *derived* closed-vocabulary state
  (`planned|blocked|failed|applied|partially_applied|verified|
  unverified|ready|certified`) computed from final
  `status`/`verification` — additive, never asserted.
- Incremental reinstall evidence: `managed_files` entries now carry the
  post-operation `sha256` and an action classified from real content
  digests (`created|updated|unchanged|removed`), plus `managed_summary`
  action counts. A repeated identical install reports the whole managed
  set `unchanged`; the account manifest is only rewritten when its
  content (minus `updated_at`) differs.
- `<state>/install/receipts/` is materialized by `ensure_workspace` so
  the directory is classified honestly in the receipt that declares it.
- Regression coverage: lifecycle derivation tables, incremental
  reinstall (no rewrites), context-manifest honesty, minimal L0 boot
  surface, host-mirror (`AGENTS.md`/`CLAUDE.md`) invariants sync and a
  token-value sweep over every emitted surface.

### Added — install-2.1 service surface (Cycle 16 wave 11)

- `api-sentinel install` is now a lifecycle group:
  `status` · `doctor` · `repair` · `uninstall` · `mcp-verify`, with the
  bare command still running the guided (TTY) or headless (`--yes`)
  install. `forge.json` declares `installation_kind: service`,
  `mcp_verify_tool: sentinel_health`, the full `install{}` surface and
  `depends_on: []`; packaged manifest copies ship under
  `src/api_sentinel/installation/assets/manifest/` for wheels.
- Canonical `scripts/forge_bootstrap.py` vendored byte-identically from
  The Forge installkit (wrappers `install.sh`/`install.ps1`/
  `install.windows.sh` likewise); `scripts/install_portable.py` stays
  as the documented legacy pip-plus-delegate route.
- `forge/InstallReceipt/v1` on `--json` and persisted under
  `<state>/install/receipts/` — `managed_files`, `checks`
  (PASS/FAIL/BLOCKED/UNVERIFIED/NOT_APPLICABLE) and honest
  `verification.status`; dry-run reports `planned` + `UNVERIFIED`.
- Account manifest merge-write at `~/.forge/installations/` so a
  CLI-only install stays discoverable (`FORGE_HOME_OVERRIDE` honored).
- Real MCP verification: `initialize → initialized → tools/list →
  tools/call sentinel_health` over stdio, with BLOCKED/UNVERIFIED for
  missing exe/extra — never fabricated PASS.
- `workspace` scope: state under the discovered workspace root
  (`forge.workspace.*`/`.forge-workspace`/workspace pyproject/VCS
  markers) + read-only depth-1 member discovery, explicitly deferred
  (members are observation targets, not install targets).
- Global `api-sentinel --version` (the bootstrap smoke check).

### Changed — install-2.1 contract

- `--profile` now means the install tier
  (`minimal|recommended|full`); the runtime profile moved to
  `--runtime-profile` (`local|aws|opentelemetry`). Runtime values on
  `--profile` are still honored as a deprecated alias with a named
  warning; `full` is never a runtime value. A reinstall with a new
  runtime profile updates `config.yaml` deliberately and the receipt
  reports `previous_profile` → `effective_profile`.
- `--root` names the install target root (state at
  `<root>/.api-sentinel`, context registry at the root); `--workspace`
  keeps naming the verbatim state dir. Together they are refused as
  conflicting targets; a non-`.api-sentinel` state dir skips context
  registration with a named note instead of writing foreign roots
  (fixes the `ws.root.parent` bug that could write `/tmp/AGENTS.md`).
- `--host` is validated + recorded `NOT_APPLICABLE` (a service forge
  writes no host assets); `--components` is honestly recorded with
  unhonored names; `--gateway-profile`/`--gateway` is persisted as
  advisory (no gateway mode). `forge.agentic.json` no longer lists
  fabricated agents/host adapters.

### Fixed — install-2.1 safety

- Non-interactive install/uninstall/repair without `--yes`/`--dry-run`
  is a named refusal (exit 2) instead of a silent install.
- `uninstall` is ownership-gated: sentinel markers required, fs-root
  and `$HOME` targets refused; default keeps data, `--purge` removes.
- `update` refuses an existing-but-uninitialized dir (`config.yaml` is
  the installed marker; the old guard let it silently create a db).
- `install repair` re-registers a missing capability manifest
  (`repair_context` alone rebuilds the registry from present files and
  would silently drop this forge).
- `install status/doctor` aggregate honestly: UNVERIFIED/BLOCKED
  checks degrade the verdict instead of reading as `healthy`.
- The token secret is never serialized into receipts — only its path.

## [0.1.2] - 2026-11-13

### Fixed

- Unified platform release: SpecialistCapability/v1 contract boundary fix, mcp_verify_tool declaration.


## [0.1.1] - 2026-11-12

Cycle 2.7 patch release: federated SDD contracts and adapters, forge-context kind taxonomy, universal Obsidian export verbs, portal and install UX improvements, provider context-registry wiring, CI hardening and generated-artifact refresh.


### Cycle 10 — cross-forge integration, operational UX & production readiness

Wave-closed homologation loop (specs: `factory/specs/archive/cycle-10-*`;
evidence: `docs/cycles/cycle-10/`). Package version stays `0.1.0`; the
planned tag after merge is `v0.1.0-rc.2` (rc line per the versioning
policy — see `docs/cycles/cycle-10/18-release-readiness.md`).

#### Added

- `/v1` real authentication: workspace bearer token
  (`secrets.token_urlsafe(32)`, `hmac.compare_digest`, env → stdin →
  file precedence, optional TTL rotation) + HMAC-signed integrated
  principal (±300 s skew, single-use nonce, workspace match). Serving
  is never credential-less (F007).
- `api-sentinel mcp serve` — read-only stdio MCP server (`mcp` extra):
  `sentinel_list_services`, `sentinel_capabilities`, `sentinel_health`,
  `sentinel_list_incidents` (F012).
- `integrations/atlas/store_adapter.py` — `DbStructuralContext`
  consumes a real Code Atlas SQLite index read-only, READY-snapshot
  gated, exact-identity resolution only (F011).
- `adapters/apisentinel` — stdlib-only `forge/v1` provider adapter for
  the-forge; all 8 declared capability/action pairs execute
  (F010/F015/F018).
- Ingestion batching: `get_many` / `idempotency_keys` /
  `overlapping_sources_in_ranges` repository ports — dedup/service/
  overlap point queries collapsed to constant-size batches (F006).

#### Fixed

- Stale `ProjectContext` import in the Doctor ACL (moved to
  `forge_doctor_api.core.context` at doctor 0.3.1); real in-process
  delegation proven — 10 findings / 9 unknowns (F009, F003).
- `InvestigationHandoffV1` requiredness aligned with the published
  schema — `payload`/`constraints` required in the model (F014).
- `handoff ingest` requires explicit `handoff_version` — arbitrary JSON
  no longer ingests as a Doctor bundle (F022).
- ForgeManifest declares only executor-backed pairs (F015);
  ExecutionResult findings re-emit evidence refs as declared Evidence
  records (F018).
- Lint debt in `scripts/cov_core.py` (F013); README Cycle-1 staleness
  (F001); demo exporter's contract-illegal `measurement.temporality`
  (F023).

#### Security

- Self-declared `X-Sentinel-Role` is dead: every `/v1/*` route except
  `/v1/health` requires a verified credential; `401` + problem+json on
  failure; CORS deny-by-default; forwarded headers never consulted.
- Handoff/file boundary hardened: correlation ids end-to-end,
  transport_state ≠ execution_state, path-traversal guards
  (`resolve_under_root`), bounded reads (32 MiB), ingest correlation
  checks.
- §12 battery: 40/40 controls held — `evidence/security-results.json`.

#### Documentation

- `docs/cycles/cycle-10/` — full wave record (00–18) + consolidated
  evidence manifest; findings ledger F001–F024 final states;
  release-readiness gates A–G scored; final report A–K.
- `docs/16-compatibility-matrix.md` refreshed with verified versions.
- Portal counterpart: `forge-portal@cycle-10-sentinel` proves
  `/api-operations` on live Sentinel `/v1` data (19/19 browser steps).

## [0.1.0-rc] - 2026-11-09

Release candidate — cycles 0–9 consolidated. `0.1.0` stays the package
version; the RC label lives here only, per the versioning policy.

### Added

- Cycle 0 foundation: canonical docs (`docs/00`–`17`), ADR-001…018,
  versioned JSON Schemas under `contracts/v1/`, capability-ownership
  matrix, threat model, E2E strategy and technical plan.
- Cycle 1 core: installable `api_sentinel` package; typed domain models
  (service/environment/operation identity, telemetry sources, observation
  batches, evidence artifacts); versioned SQLite migrations and
  repositories; workspace control (project/user scope); evidence storage
  with SHA-256 integrity; structured secret-free logging; secret redaction
  helpers.
- CLI `api-sentinel` with `install`, `doctor`, `status`, `capabilities`,
  `services`, `sources`, `workspace`, `version` and a `--json` mode;
  documented exit codes.
- FORGE ecosystem manifests: `forge.json`, `forge.agentic.json`, and a
  `forge-knowledge` package prepared for registration in `the-forge`;
  loop-factory specs for Cycles 2–9 under `factory/specs/`.
- Cycle 2 telemetry ingestion: JSONL/OTLP-JSON importers, normalization
  pipeline, batch idempotency + per-source checkpoints, bounded quarantine
  (redacted payloads, truncation flags), ingestion jobs and provenance.
- Cycle 3 runtime health: honest `AVAILABLE`/`UNAVAILABLE`/`PARTIAL`/
  `UNVERIFIED`/`UNKNOWN` states, SLI/SLO definitions, error-budget
  evaluation, staleness conditions.
- Cycle 4 detection + incidents: deterministic detectors with versioned
  rules, anomaly dedup, append-only `incident_events` lifecycle, evidence
  requirements.
- Cycle 5 cross-forge intelligence: Atlas/Lens/Doctor/data-sentinel
  integration seams with named `AVAILABLE`/`UNAVAILABLE`/`UNVERIFIED`
  states and handoff-file envelopes.
- Cycle 6 portal API: FastAPI `serve` on loopback, `X-Sentinel-Role`
  placeholder scopes, query/investigation/handoff routes, portal views.
- Cycle 7 AWS read-only observability: optional `aws` extra, CloudWatch/
  API Gateway collectors, simulated offline lab (LAB-013), receipts.
- Cycle 8 agentic investigation: hypothesis lifecycle, typed verification
  plans, change intelligence, governed agent sessions via Bridge
  handoffs.
- Cycle 9 production hardening: `backup create/restore` (verified
  manifests, refusal without `--force`), `retention` policy enforcement
  (dry-run default, evidence protection, journaled receipts), Sentinel
  self-observability (`sentinel` block in `doctor` + additive `/v1/health`
  field), scale benchmark (`lab/bench/`), authorization matrix
  (LAB-016), degradation matrix, contract compatibility matrix,
  backup/restore recovery proof (LAB-014), socket-blocked offline test
  proof, and Windows+Linux CI.
