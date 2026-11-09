# Cycle 10 — Executive summary (Wave 0 seed)

> Seeded by Wave 0 (reality audit). Sections marked *pending* are filled by
> later waves per `factory/specs/active/cycle-10-wave0-baseline.md`.

## What Cycle 10 is

Per `prompt_evo_cycle10.md`: cross-forge integration closure, operational UX
and production readiness. Phase 0 requires a **reality audit before any
change** — this wave is that audit: baseline, revalidated findings, the
capability stage matrix and the cross-repo contract inventory. **Docs and
evidence only — no product code changed.**

## Baseline facts (verified at audit time)

| Fact | Value | Evidence |
|---|---|---|
| Branch | `cycle-10` @ `29375e3f` | `git branch --show-current` |
| Version | `api-sentinel-forge 0.1.0` (RC, cycles 0–9) | `src/api_sentinel/__init__.py`, `CHANGELOG.md` |
| Test suite | **440 passed in 58.45s** | `evidence/baseline-tests.txt` |
| E2E smoke | **15/15 PASS** (`lab/e2e_validate.py --smoke`) | same file |
| mypy | clean — 112 source files | same file |
| ruff | 2 errors (scripts/cov_core.py only) | same file |
| Wheel install | clean build + fresh-venv run OK | `evidence/installation-results.json` |
| Bench 10k | sequential **1075.6 obs/s**; dispersed-1440 **934.9 obs/s** | `evidence/performance-before.json` |
| Sibling repos | 10 audited, all present, all `.venv` (portal n/a — Node) | `evidence/baseline.json` |
| Peer modules in sentinel venv | **none** of the 7 forge packages importable | baseline.json `optional_modules_in_venv` |

## Headline findings

- **9 P1 / 1 P2 / 2 P3 / 1 refuted — 0 P0.** Full ledger: `02-revalidated-findings.md`.
- Sharpest five:
  1. **F009** — the in-process Doctor path is *stale*: it imports
     `ProjectContext` from `forge_doctor_api.handoff.context`, but doctor 0.3.1
     moved it to `core.context`. Even with the package installed, the real
     execution path dies with ImportError → `status=failed`.
  2. **F010** — `SentinelBoundary` advertises 7 execute pairs to The Forge;
     `_EXECUTORS` implements 2. 5 of 7 refuse `APISENT-NO-EXECUTOR`.
  3. **F011** — `api-sentinel/AtlasExport/v1` is an orphan: nothing in
     code-atlas-forge emits it (grep → 0 hits). Structural correlation has
     only ever seen a sentinel-authored lab fixture.
  4. **F012** — `forge.agentic.json` declares `mcp_command: [cli, mcp, serve]`
     but `api-sentinel mcp` does not exist (no MCP code in src/).
  5. **F005** — Portal `/api-operations` is wired to *Product Forge*'s
     `forge/PortalApi/1` backend, not the Sentinel `/v1` surface — and the two
     envelopes are structurally incompatible (problem+json vs PortalApi).
- **F002 refuted**: `cli:main` and `cli:app` are both real, importable,
  working objects — different contracts, not a divergence.
- **F006 quantified**: 1 SELECT per observation (dedup) + 1 SELECT per
  distinct window (overlap) = 12,880 read queries for a 10k dispersed ingest.
- **F007 confirmed**: role is a raw client header; bounded by loopback
  default + documented placeholder → P1, the top Phase-1 fix.

## Stage-matrix headline

Local surface (CLI, ingestion, detection, incidents, SLO, backup, retention,
/v1 API) is **VERIFIED** — 440 tests + 15/15 e2e at HEAD. Federated surface is
mostly **DECLARED→TESTED**, not INTEGRATED: every peer transport is
file-emission or named-unavailable; nothing executes a peer today. Full
matrix: `03-capability-ownership.md`.

## Pending (later waves)

- Final executive summary text, gates A–D status, release recommendation.
