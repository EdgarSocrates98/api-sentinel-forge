# Cycle 16.1 — Wave 12 report: api-sentinel-forge

- Spec: `factory/specs/inbox/cycle16_1-wave12-api-sentinel-forge.md`
- Branch: `cycle16_1/wave12` (off `main@11c5b6c`)
- Date: 2026-11-19 · Agent: devin subagent

## Scope → evidence

| Requirement | Status | Evidence |
|---|---|---|
| Receipts/status compatible with `forge/InstallLifecycle/v1` | DONE | New `src/api_sentinel/installation/lifecycle.py` — self-contained port of the shared derivation model (no `theforge` import; contract is documents-only). Every emitted receipt (`install`, `uninstall`, `repair`, incl. `--dry-run` plans) and health doc (`install status`, `doctor`, `mcp-verify`, `repair --dry-run`) carries `lifecycle: {schema, state, detail}` — derived at the emit boundary, never asserted. Verified live: install→`verified`, status→`ready`, absent→`unverified`, dry-runs→`planned`. |
| Incremental reinstall test | DONE | `managed_files` now carries post-op `sha256` + `created\|updated\|unchanged\|removed` actions from real before/after content digests; `managed_summary` counts actions. `register_installation` only rewrites the account manifest when content (minus `updated_at`) differs. Empirical + test: identical reinstall → `created:0, updated:0, unchanged:12`; `--runtime-profile` change → only `config` `updated`. |
| Bearer token never in receipts/stdout (PR #15 / 6d2a67d) | VERIFIED (tests existed) | `test_install_never_emits_token_value` + `test_serve_never_emits_token_value` already merged in #15; kept green. Added `test_lifecycle_surfaces_never_emit_token` sweeping status/doctor/stdout. |
| Context metadata honest; minimal L0 boot | DONE | `test_context_manifest_metadata_is_honest` — emitted `forge/context/v1` validates (`validate_manifest==[]`), `installed.components` mirrors persisted `config.yaml`, registry entry sha256 matches the manifest bytes. `test_l0_boot_surface_is_minimal` — AGENTS.md managed blocks bounded (<8 KiB total), CLAUDE.md thin pointer (<2 KiB), AGENT_PROTOCOL.md consolidated; L0 advertises where L1 lives. |
| Host surfaces sync verified | DONE | `test_host_instruction_mirrors_stay_in_sync` — `sentinel:invariants` block byte-identical in `AGENTS.md`/`CLAUDE.md` (verified: identical). |
| Docs updated to actual parser; run changed examples | DONE | `docs/12-installation-and-distribution.md` §Receipts: lifecycle block + incremental semantics; `CHANGELOG.md` Unreleased entry. Examples executed live: `install --root ./proj --scope project --runtime-profile local`, `install status`, `install doctor` (real MCP handshake, 4 tools), `repair --dry-run`, `uninstall --dry-run`, `api-sentinel doctor` (exit 0). |

## Acceptance criteria from the wave spec

- Perfis runtime×instalação — pre-existing split (`--profile` tier vs `--runtime-profile`) kept; honesty covered by `test_reinstall_with_change_reports_updated_only`.
- Token redaction — covered above.
- Migrações — `migrations` check unchanged; 6/6 applied on fresh + reinstall.
- Workspace isolation — wave-11 tests kept green (`test_arbitrary_state_dir_never_writes_context`, `test_user_scope_skips_context`, member discovery read-only).
- Context discovery / segurança / API operations / service health / integração API Forge — no delta required; surfaces verified via existing suite.

## Changes

- `src/api_sentinel/installation/lifecycle.py` (new, +165): `forge/InstallLifecycle/v1` derivation — `receipt_state`, `health_state`, `receipt_block`, `health_block`, closed `STATES` vocabulary.
- `src/api_sentinel/installation/__init__.py` (+24): `write_receipt` derives the lifecycle block before persisting (same dict emits to stdout); `register_installation` returns `(path, changed)` and skips rewriting byte-identical manifests.
- `src/api_sentinel/cli/install.py` (+120): `_emit_receipt`/`_health_doc`/`mcp-verify`/`repair --dry-run` attach derived lifecycle; `_managed_digests`/`_digest_path`/`_classify_managed`/`_managed_files`/`_managed_summary` honest classification; account-registry check reports `updated|unchanged`.
- `src/api_sentinel/workspace.py` (+4): `ensure_workspace` materializes `install/receipts/` so the declared managed dir is classified honestly.
- `tests/integration/test_install_lifecycle.py` (new, 31 tests): lifecycle blocks, derivation tables, incremental reinstall, context-manifest honesty, L0 boot, host-mirror sync, token sweep.
- `docs/12-installation-and-distribution.md`, `CHANGELOG.md`.

## Gates

- `pytest -q`: **694 passed, 12 skipped, 1 failed** — the failure is `test_catalog_command_governed_outcome[aws collect]`, a pre-existing environment issue (`botocore[crt]` missing in this venv) reproduced identically on clean `main@11c5b6c` — unrelated to this change.
- `ruff check .`: clean. `mypy src`: clean (130 files).
- Build: `uv build` → `api_sentinel_forge-0.1.2` wheel + sdist. (`python -m build` unavailable — `build` is not a declared dev dep in this repo's venv.)
- `api-sentinel doctor`: exit 0.

## Observed gaps (reported, not silently fixed — feature freeze)

- `api-sentinel update` emits an ad-hoc JSON result (`{"updated": …}`), not a `forge/InstallReceipt/v1` — so no lifecycle/receipt evidence on that path. Candidates for a later wave.
- `managed_files[].kind` uses forge-local kinds (`state-dir`, `db`, `token`, …) richer than the strict InstallReceipt schema enum — consumers read them leniently; kept as-is intentionally.

## Verdict

DELTA IMPLEMENTED — receipts/status now emit `forge/InstallLifecycle/v1`,
incremental reinstall proven byte-honest, all wave checks green.
