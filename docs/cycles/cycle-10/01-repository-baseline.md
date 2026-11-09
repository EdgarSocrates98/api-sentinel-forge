# Cycle 10 — Repository baseline (Wave 0)

Machine-readable source of truth: `evidence/baseline.json` (collected
2026-11-09, `gh` authenticated as EdgarSocrates98). All paths are read-only
audits — no sibling repo was modified and no sibling dev env was installed.

## Baseline table

| Repo | Branch (default) | HEAD | Version | Dirty | Dev env | Notes |
|---|---|---|---|---|---|---|
| **api-sentinel-forge** | cycle-10 (main) | `29375e3` | 0.1.0 | 0 | ✅ .venv py3.12.13 | no forge-* peer module importable |
| the-forge | phase2/baseline-observability-20261108 (main) | `6dfb79f` | 0.7.4 | 16 (untracked audit/memory artifacts) | ✅ | ExecutionResult v1 + forge/v1 envelope at contracts/ |
| forge-doctor-api | main | `fe99013` | 0.3.1 | 1 (.codebase-memory) | ✅ | DoctorBoundary/ForgeRequest v2 live; ProjectContext in `core.context` |
| api-forge | fix/control-plane-windows-concurrency-20261108 (main) | `029b9aa` | 0.3.2 | 1 | ✅ | apiforge/* v1 family |
| code-atlas-forge | main | `12bdc39` | 1.1.1 | 2 | ✅ | **no AtlasExport/api-sentinel refs** (C10-F011) |
| forge-agent-bridge | fix/phase1.1-fast-provider-catalog-20261108 (main) | `92df03f` | 0.2.1 | 1 (uv.lock) | ✅ | AgentBridgeContract/v1 + session ctx v2 |
| forge-lens | main | `5687788` | 0.2.1 | 0 | ✅ | SpecialistAgenticManifest/v1 |
| data-sentinel-forge | main | `e222af8` | 0.5.2 | 3 (evidence JSONs) | ✅ | doctor-correlation/v1 declared |
| forge-portal | feat/api-operations-contract-surface-20261108 (main) | `1e85c28` | 1.1.1 | 101 (e2e artifact PNGs) | n/a (Node) | /api-operations → product-forge PfApi, **no sentinel wiring** |
| product-forge | fix/bridge-embedded-parity-20261108 (main) | `2660d39` | 0.2.2 | 7 | ✅ | PortalApi/1 envelope + session auth |

## Relevant PRs (recent, `gh pr list --limit 5`)

- **the-forge**: #191 OPEN "phase 2 baseline and ownership" (the current
  branch's PR), #190 MERGED "native MCP server + install integration".
- **forge-doctor-api**: #100/#97/#96 OPEN dependabot/deps only; #99 MERGED
  specialist roster.
- **api-forge**: #119 OPEN validation PR for the checked-out branch; #118
  MERGED "serialize concurrent control-plane transitions".
- **forge-portal**: #88 MERGED "governed API operations investigation
  surface" — the /api-operations page audited for C10-F005.
- Others: roster/knowledge/parity merges; nothing touching the sentinel
  contract surface.

## Sentinel local-health snapshot (verbatim `doctor --json`, fresh venv)

- capabilities: **13 available / 1 declared** (`api.agentic-investigation`).
- integrations: `the-forge` available via `local_boundary` (the seam is
  in-process in this repo); `forge-doctor-api`, `forge-agent-bridge`,
  `forge-lens`, `data-sentinel-forge`, `api-forge` → `unverified` (module not
  importable, handoff_file/file transport only); `code-atlas-forge` →
  `unavailable` (no atlas export configured).
- DB: 6 migrations applied, integrity ok, all tables empty on fresh install.

## Install state notes

- Sentinel `.venv` contains fastapi/uvicorn/boto3/mcp but **zero** sibling
  forge packages — every peer ACL therefore resolves to its file/unavailable
  transport today. This single fact anchors F003, F004, F008.
- forge-portal has no Python env (Angular/Node); its 101 dirty files are
  generated e2e captures, not source edits.
