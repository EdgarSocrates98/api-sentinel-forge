---
cycle: 10
deliverable: PR plan — three repos, titles/bodies/diff stats, merge order, no PRs opened
wave: 5
---

# Cycle 10 — PR plan (artifacts only — **no PRs opened, no pushes**)

Per spec AC + §19: drafts below are ready for `gh pr create`; the actual
open is an owner action. `gh` is authenticated on this host
(`evidence/baseline.json` recorded EdgarSocrates98). Merge order:
**api-sentinel-forge first** (it is the evidence source), then
forge-portal + the-forge in either order.

Agent disclosure (repo norms): all cycle-10 work was agent-assisted;
wave commits carry messages naming the wave and findings; the wave-5
commit is authored by `Devin` (bot account). PR bodies keep an explicit
"Agent involvement" section — do not strip it.

## PR 1 — api-sentinel-forge `cycle-10` → `main`

**Diff:** 9 commits · 213 files · **+23 980 / −144**
(`git diff main...cycle-10 --stat`; merge-base `1c616a5` = main HEAD).

By area: `src/` 30 files (auth, batching, atlas store adapter, MCP
server, boundary executors, capabilities) · `tests/` 11 (+2 700 lines of
new batteries) · `docs/` 27 (+ `docs/cycles/cycle-10/` ~123 files incl.
all evidence receipts) · `lab/` 12 (federated runner, bench matrix,
security battery, fixtures) · `adapters/` + `factory/specs` + `contracts`
+ `pyproject.toml` + `.gitignore`.

Commits: `29375e3` scaffold · `4569cd9` wave0 · `6087d69` wave1 ·
`de5e9df`+`6c009e8` wave2 · `af274f8` wave4-spec · `97dfc66` wave3 ·
`b47f409` wave4 · `e7a9323` spec lifecycle · `<wave5>` this commit.

**Title:** `Cycle 10: cross-forge integration closure, /v1 auth hardening, federated E2E, release readiness`

**Body draft:**

```markdown
## Summary

Cycle 10 closes the cross-forge homologation loop for the 0.1.0 release
candidate (prompt: `prompt_evo_cycle10.md`; spec chain:
`factory/specs/archive/cycle-10-wave{0..5}-*.md`).

- **Wave 0** — reality audit: baseline, findings ledger F001–F024,
  capability stage matrix, contract inventory. No product code.
- **Wave 1** — /v1 real auth (workspace bearer + HMAC integrated
  principal; self-declared `X-Sentinel-Role` → 401), handoff/file
  boundary hardening, `api-sentinel mcp serve`, contract/storage
  negatives. F007/F012/F013 fixed.
- **Wave 2** — real federation: Doctor 0.3.1 in-process run (F009),
  Code Atlas real index-store seam (F011), the-forge provider with all
  8 execute pairs + routed receipts (F010/F015/F018), InvestigationHandoff
  requiredness aligned (F014). Bridge/Lens/api-forge/data-sentinel
  honestly named unverified/unavailable.
- **Wave 3** — Portal `/api-operations` on live Sentinel /v1 via the
  dedicated `SentinelApi` adapter (portal repo), same-origin /sentinel
  proxy pattern for CSP `connect-src 'self'`. F005 fixed, F019–F021
  recorded.
- **Wave 4** — federated E2E S1–S18 (16 pass / 2 honestly BLOCKED),
  F006 N+1 closed (2 880→2 overlap queries on dispersed), §12 security
  battery 40/40, bounded real AWS validation (STS-gated, 15 calls).
- **Wave 5** — release readiness: gates A–G scored, capability stage
  manifest, packaging re-verified, README/CHANGELOG/compat-matrix
  refreshed, PR plan + final report. **CONDITIONAL GO** (conditions:
  hosted CI green on push, PR-scope decisions — see
  `docs/cycles/cycle-10/18-release-readiness.md`).

## Test plan

- `pytest -q` — **544 passed** · `ruff check .` clean · `mypy src` clean
  (116 files) · `uv build` clean (wheel 127 files: 6/6 migrations;
  sdist 190 files: tests + 8 JSON schemas) · fresh-venv install smoke
  PASS (`evidence/installation-results.json`).
- Real-runtime receipts: `docs/cycles/cycle-10/evidence/` — doctor
  in-process run, atlas correlate, the-forge routed runs, portal
  19/19, federated-e2e receipt, security-results, aws-validation.

## Agent involvement

Agent-assisted throughout (waves run under loop-factory specs); wave-5
release-closure commit authored by Devin. All external claims cite
receipts in `docs/cycles/cycle-10/evidence/`.
```

## PR 2 — forge-portal `cycle-10-sentinel` → `main`

**Branch reality:** 22 commits ahead of merge-base `e68dd8e`; main is at
`324b628` (merge of PR #88 — content the branch already contains). The
**sentinel change is commit `52a21e6` only** — 23 files, **+4 442 / −584**
(new `src/app/sentinel/*`, rewritten `/api-operations` page subtree,
i18n keys, e2e harness + artifacts). The other 21 commits are unrelated
Obsidian/knowledge work by other sessions.

**Options** (owner decides):
- **A (recommended)** — land the branch as one PR (it is what was
  verified); the sentinel surface is the head commit.
- **B** — cherry-pick `52a21e6` onto a fresh branch from `main` for an
  api-operations-only PR; the e2e + i18n hunks ride along inside it.

**Title (option B):** `feat: power /api-operations from the real API Sentinel runtime`

**Body draft:**

```markdown
## Summary

Repoints `/api-operations` at the real API Sentinel `/v1` surface via a
new dedicated `SentinelApi` injectable — `PfApi`/`forge/PortalApi/1`
untouched; the page renders fully with Product Forge absent
(independence verified in the e2e).

- Three tabs (`?tab=services|incidents|status`, `&service=`/`&incident=`
  deep links): inventory, service detail (indicators w/ units,
  absent_indicators, PF correspondence verdict, operations provenance,
  metrics windows, SLO cards, telemetry sources), incidents (filter,
  detail w/ hypothesis ladder + merged timeline), scope-gated
  investigation POST (403 → named blocked state), Sentinel status
  (health, capability stages, integrations, source freshness).
- Honest-state vocabulary end-to-end (`ui-state-badge` additive
  `kindOverride`; `—` for null, never a fabricated zero).
- Bearer token via `Authorization` only — sessionStorage, localStorage
  scrubbed, never in URL/DOM/console (asserted).
- i18n: ~180 `sentinel.*` keys, en+pt-BR parity.
- Deployment pattern discovered + shipped: same-origin `/sentinel`
  reverse proxy (CSP `connect-src 'self'` makes cross-origin fetches
  impossible — C10-F019).

## Test plan

- `npm test` — 624 passed / 57 files (23 new specs).
- `e2e/test_sentinel_e2e.py` — real CLI-seeded workspace + real
  `api-sentinel serve` + served bundle + Chromium: **19/19 steps**,
  7 screenshots under `e2e/artifacts/`, `token_in_artifacts: false`.
- a11y: 11 checks green; keyboard + aria-live covered.

## Agent involvement

Implemented in api-sentinel-forge Cycle 10 Wave 3 (C10-F005); receipts
in that repo at `docs/cycles/cycle-10/evidence/portal-verification.json`.
```

## PR 3 — the-forge `cycle-10-apisentinel` → `main`

**Branch reality:** ~90 commits ahead of `main` (`fad8122`) — the branch
carries the whole phase-2 line plus docs(phase2) commits. The
**api-sentinel change is commit `5aea039` only** — 9 files, **+563 / −3**
(`adapters/apisentinel/` new stdlib-only distribution + `pyproject.toml`
mypy path/ignore_missing_imports per the adapters/ convention).
Feature-freeze compliant: it is an `adapters/` specialist-surface
compat addition, not a core change (`src/theforge` untouched by it).

**Options** (owner decides — the branch's non-sentinel history is out of
this cycle's scope):
- **A (recommended, adapter-only)** — cherry-pick `5aea039` onto a new
  branch from `main` (e.g. `feat/apisentinel-adapter`), PR that. Keeps
  the-forge's freeze + one-problem-per-PR clean.
- **B** — PR the whole `cycle-10-apisentinel` branch; body must then
  disclose it also lands the phase-2 line.

**Title (option A):** `feat(adapters): api-sentinel forge/v1 provider adapter`

**Body draft:**

```markdown
## Summary

Stdlib-only `forge/v1` provider adapter exposing API Sentinel's
`SentinelBoundary` to The Forge: describe / health / capabilities /
execute over the specialist's own public surface. All 8 declared
capability/action pairs execute — discover → route → execute →
validate → receipt proven against the real runtime in api-sentinel-forge
Cycle 10 Wave 2 (run ids `20261109T13*`; evidence in that repo at
`docs/cycles/cycle-10/evidence/theforge/`).

- `adapters/apisentinel/` — separate distribution
  (`theforge-apisentinel-adapter==0.1.0`), stdlib-only per the adapters/
  contract; recorded `native_surface.json` + `catalog_verification.json`
  (5/5 supported).
- `pyproject.toml` — mypy path + `ignore_missing_imports` for the new
  package, per the adapters/ convention.
- No `src/theforge` changes — feature-freeze compliant adapter lane.

## Test plan

- Adapter conformance battery in api-sentinel-forge
  (`tests/contract/test_theforge_adapter.py`, 5 tests) + live
  `theforge registry refresh` → `state=ready` + routed `ask` runs with
  `verification.json status=passed` and persisted receipts.
- `theforge provider check` probes execute/handoff against an empty
  scratch workspace — storage-backed providers honestly refuse
  `APISENT-WORKSPACE-UNAVAILABLE` there (recorded: C10-F016; peer-side
  recommendation for a fixture-root probe tier deferred).

## Agent involvement

Authored in api-sentinel-forge Cycle 10 Wave 2 (spec
`factory/specs/archive/cycle-10-wave2-federation.md`).
```

## Push / open checklist (owner)

1. `git push -u origin cycle-10` (sentinel) → watch `ci` workflow → then
   `gh pr create` with the body above.
2. Decide portal option A/B; push + open accordingly.
3. Decide the-forge option A/B; if A, cherry-pick `5aea039` onto
   `feat/apisentinel-adapter` first.
4. After all merges: tag `v0.1.0-rc.2` on `main` (per
   `14-packaging-installation.md` §4) — **not tagged yet**.
