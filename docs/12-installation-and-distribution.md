# 12 — Installation & Distribution

## Install-2.1 service surface (Cycle 16 wave 11)

API Sentinel is an **`installation_kind: service`** forge — installing
means materializing a governed *state workspace* (SQLite + config +
token), not copying content packs. The install writes **no host assets**
(no `.claude/agents`, no host-specific files): `--host` selections are
accepted, validated and recorded `NOT_APPLICABLE`.

### Scopes and roots

`--root PATH` names the **target root** — the project or workspace the
Sentinel serves. The state dir is always `<root>/.api-sentinel/` and the
forge-context registry (`.forge/context/` + consumer doc blocks) lands
at the target root. An explicit `--root` is authoritative: discovery
never substitutes an ancestor marker for it.

`--workspace PATH` names the **state dir, verbatim** — the same flag
every other command (`status`, `ingest`, `serve`, …) uses. When the
path is literally named `.api-sentinel`, its parent is the context
root; otherwise context registration is honestly skipped with a named
note (pass `--root` for registration). `--root` + `--workspace`
together are refused as conflicting targets.

| Scope | State dir | Context registration |
| --- | --- | --- |
| `project` (default) | `<cwd-or-root>/.api-sentinel/` | at the target root |
| `workspace` | `<workspace-root>/.api-sentinel/` | at the workspace root |
| `user` | `~/.api-sentinel/` | none — account level, no workspace docs |

`workspace` scope discovers the nearest ancestor carrying
`forge.workspace.{yaml,json}`, `.forge-workspace`, a
`tool.forge.workspace`/`tool.uv.workspace` table, or a VCS root.
Members (child dirs with `.git`/`pyproject.toml`/`package.json`/
`forge.json`, depth 1, no symlinks) are discovered **read-only** and
recorded as *observation targets, not install targets* — the Sentinel
never installs into child members silently.

Environment override: `API_SENTINEL_HOME` pins the state dir for all
commands (context root derived only when sentinel-shaped);
`FORGE_HOME_OVERRIDE` relocates the account registry
(`~/.forge/installations/`) for tests/portable layouts.

### Profile split — two different "profiles"

| Flag | Values | Meaning |
| --- | --- | --- |
| `--profile` | `minimal` · `recommended` · `full` | **install tier** — the installkit disclosure tier. One service bundle ships, so all tiers install the same artifacts; the tier is recorded, never expanded silently. |
| `--runtime-profile` | `local` · `aws` · `opentelemetry` | **runtime profile** — persisted as `profile:` in `config.yaml` (telemetry/integration posture). `full` is never a runtime value. |
| `--gateway-profile` / `--gateway` | `economy` · `balanced` · `deep` | persisted as `gateway_profile:` — **advisory**: the Sentinel has no gateway mode. |
| `--components` | csv | accepted + honestly recorded; the service bundle has no optional components (`NOT_APPLICABLE`, unhonored names reported). |

Backwards compatibility: a runtime value on `--profile`
(`local`/`aws`/`opentelemetry`) is still honored as `--runtime-profile`
with a named deprecation warning — recorded in the receipt's
`warnings`. Unknown values on either flag stay usage errors (exit 2).

A reinstall with a different `--runtime-profile` updates `config.yaml`
deliberately and the receipt reports `previous_profile` →
`effective_profile` — the requested value is never reported as
effective without the write landing.

### Flag family (delegation contract — matches `forge.json.install.flags`)

```
api-sentinel install [--scope project|workspace|user] [--root PATH]
                     [--workspace PATH] [--profile TIER]
                     [--runtime-profile local|aws|opentelemetry]
                     [--host claude|devin|codex|copilot|all|none]
                     [--components csv] [--gateway-profile econ|bal|deep]
                     [--yes] [--dry-run] [--json]
```

- `--yes` — headless approval. Non-interactive stdin without `--yes`
  or `--dry-run` is a named refusal (exit 2), never a silent write.
- `--dry-run` — emits the receipt with `status: planned`,
  `verification.status: UNVERIFIED` and `planned_files`; writes nothing.
- `--json` — the receipt is the only stdout payload (warnings go to
  stderr), so orchestrators parse it cleanly.

### Lifecycle ops

```
api-sentinel install            # guided (TTY) or headless (--yes)
api-sentinel install status     # probe health: state, db, migrations,
                                # token, registrations — read-only
api-sentinel install doctor     # status + python + MCP handshake probe
api-sentinel install repair     # converge a degraded install (idempotent:
                                # migrations re-applied, token reused,
                                # missing registration re-installed)
api-sentinel install uninstall  # detach; keeps state dir by default
api-sentinel install uninstall --purge   # also removes the state dir
api-sentinel install mcp-verify # real handshake: initialize → tools/list
                                # → tools/call sentinel_health
```

`uninstall` is ownership-gated: the state dir must carry a sentinel
marker (`config.yaml`, `sentinel.db`, `token`, or a known subdir) —
unmarked dirs are refused, and filesystem-root/home targets are always
refused. Default keeps data; `--purge` explicitly permits removal.
Context and account registrations are removed transactionally in both
modes. Top-level `api-sentinel uninstall` shares the same code path;
`api-sentinel update` re-applies migrations + refreshes the token and
refuses a dir without `config.yaml` (an existing-but-uninitialized dir
is not an install).

### Receipts and registries

Every mutating op emits `forge/InstallReceipt/v1` (`--json` on stdout,
persisted under `<state>/install/receipts/<op>-<ts>.json`) with
`managed_files`, `checks` (PASS/FAIL/BLOCKED/UNVERIFIED/NOT_APPLICABLE)
and `verification.status` — PASS only when every check is PASS or
NOT_APPLICABLE. Domain handoff/retention receipts under
`<state>/receipts/` are a different layer.

Three registry layers stay distinct:

- `<state>/install/receipts/` — this forge's install receipts;
- `<target>/.forge/context/` — the workspace context registry
  (`forge/context/v1`, transactional via the vendored engine);
- `~/.forge/installations/api-sentinel-forge.json` — the account
  manifest (`forge/InstallationManifest/v1`) The Forge scans. The
  canonical `scripts/forge_bootstrap.py` route writes it too; a CLI
  install merge-updates it so a CLI-only install stays discoverable.

### Bootstrap routes

| Route | Status |
| --- | --- |
| `./install.sh` · `install.ps1` · `install.windows.sh` | canonical wrappers → `scripts/forge_bootstrap.py` (isolated venv + launcher + smoke `--version` + account registry + bootstrap receipt) |
| `scripts/install_portable.py` | **legacy** pip-plus-delegate route — kept for environments that vendor their own interpreter; documented, not the primary path |
| `pip install api-sentinel-forge` + `api-sentinel install` | CLI-only path — state + registrations via the in-package engine |

### Guided install

The main path is a single command with no mandatory flags in an
interactive environment:

```
api-sentinel install
```

The wizard asks for scope and runtime profile, validates the target and
reports which checks actually ran. Nothing external is required: the
core runs offline against local files.

| Wizard choice | Options |
| --- | --- |
| Runtime profile | `local` (files + offline lab) · `aws` (opt-in read-only collection) · `opentelemetry` (authorized external source) |
| Install scope | `project` · `workspace` · `user` |
| Portal | integrate with Forge Portal when detected (reported, never assumed) |

## Command surface

```
api-sentinel install [status|doctor|repair|uninstall|mcp-verify]
api-sentinel doctor
api-sentinel status
api-sentinel capabilities
api-sentinel sources
api-sentinel services
api-sentinel ingest --source-id <src> --file ./telemetry.jsonl
api-sentinel detect --service-id <svc>
api-sentinel incidents
api-sentinel incident show <id>
api-sentinel serve
api-sentinel mcp serve
api-sentinel uninstall [--purge]
api-sentinel update
```

Cross-cutting requirements: `--json` mode for automation/CI, explicit
headless mode (`--yes`), documented exit codes, multiplatform support
(Windows + Linux priority). `serve` and `mcp serve` implicitly call
`ensure_workspace` — they create a missing state dir with defaults, so
running them against a fresh root is equivalent to a minimal install
without context registration.

## Execution profiles

| Profile | Resources | Use |
| --- | --- | --- |
| Minimal | CLI, import, analysis, SQLite | quick usage |
| Recommended | core, lab, local API, Portal integration | daily development |
| Full | optional connectors, expanded metrics, observability | advanced labs |
| Headless | JSON, CI, no TUI | automation |
| Enterprise-oriented | external auth, RBAC, audit, scalable storage | future stage |

The install tier flags (`--profile minimal|recommended|full`) map to
this table; today all tiers install the same service bundle — the tier
is recorded for forward expansion, not silently widened.
Enterprise-oriented is **not** a production-readiness promise for the
MVP.

## Naming

| Surface | Name |
| --- | --- |
| GitHub repo | `api-sentinel-forge` |
| Product | API Sentinel Forge |
| PyPI distribution (candidate) | `api-sentinel-forge` |
| Python module | `api_sentinel` |
| CLI | `api-sentinel` |
| MCP server | `api-sentinel-mcp` or a subcommand under the CLI |
| Provider identity | `api-sentinel-forge` |
| Portal area | API Operations |

Before publishing, the foundation cycle must verify distribution-name
availability and collisions in the manifest catalog.

## Metadata & manifests

`forge.json` and `forge.agentic.json` are generated or validated from
**one canonical metadata source**, preventing version divergence.

## Standalone guarantee

The product must work standalone: other FORGE repositories are not
required for it to function. Installation accepts standalone use,
optional The Forge integration, and automatic Forge Portal detection.

## Version policy

The package version follows the workspace version (`0.1.2` — see
`CHANGELOG.md`). Do not bump without a documented rationale recorded in
the repo (changelog/ADR), and do not commit to a stable 1.0 contract
surface before the compatibility gates close.
