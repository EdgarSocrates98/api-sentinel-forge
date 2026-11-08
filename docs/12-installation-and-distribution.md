# 12 — Installation & Distribution

## Guided install

The main path is a single command with no mandatory flags in an
interactive environment:

```
api-sentinel install
```

The wizard detects available tools, presents options, validates
permissions and reports which components were actually configured.

| Wizard choice | Options |
| --- | --- |
| Profile | `Local` (files + offline lab) · `AWS` (opt-in read-only collection) · `OpenTelemetry` (authorized external source) |
| Install scope | `project` · `workspace` · `user` |
| Portal | integrate with Forge Portal when detected (asked, never assumed) |

## Proposed command surface

```
api-sentinel install
api-sentinel doctor
api-sentinel status
api-sentinel capabilities
api-sentinel sources
api-sentinel services
api-sentinel ingest ./telemetry.jsonl
api-sentinel analyze --service payments-api
api-sentinel incidents
api-sentinel incident show INC-001
api-sentinel lab run
api-sentinel serve
api-sentinel mcp serve
api-sentinel uninstall
```

This list is proposed, not committed: names go through a naming and
compatibility audit before implementation, and not all commands land in
Cycle 1. Cross-cutting requirements: `--json` mode for automation/CI,
explicit headless mode, documented exit codes, multiplatform support
(Windows + Linux priority).

## Execution profiles

| Profile | Resources | Use |
| --- | --- | --- |
| Minimal | CLI, import, analysis, SQLite | quick usage |
| Recommended | core, lab, local API, Portal integration | daily development |
| Full | optional connectors, expanded metrics, observability | advanced labs |
| Headless | JSON, CI, no TUI | automation |
| Enterprise-oriented | external auth, RBAC, audit, scalable storage | future stage |

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

The package version follows the workspace version (`0.1.0` at
bootstrap). Do not bump without a documented rationale recorded in the
repo (changelog/ADR), and do not commit to a stable 1.0 contract
surface before the compatibility gates of Cycle 9.
