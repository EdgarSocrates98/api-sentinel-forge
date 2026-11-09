---
id: cycle-10-wave5-release
title: "Cycle 10 Wave 5 — Release closure: docs, CI, evidence manifest, PRs, final report"
agent: claude
risk: medium
verification:
  - "python -m pytest -q"
  - "python -m ruff check ."
  - "python -m mypy src"
  - "uv build"
---

# Context

Implements prompt_evo_cycle10.md §13–§21 (install/DX, CI gates, deliverables, acceptance gates A–G, Definition of Done, final report format A–K). Runs on `cycle-10` in api-sentinel-forge; reviews sibling branches (`cycle-10-apisentinel` in the-forge, `cycle-10-api-operations` in forge-portal) for PR prep.

# Acceptance Criteria

- [x] Install/DX validation per §13 — `evidence/installation-results.json` `wave5_reverification`: headless-safe cells executed (wheel+sdist fresh venvs, path-with-spaces, no-TTY `--yes`, repeat-install idempotent); interactive TTY wizard, per-profile matrix, install scripts, unicode, uninstall, native-Linux honestly named **not-run** in the artifact itself ("where feasible" honored — nothing fabricated).
- [x] Packaging proof — `14-packaging-installation.md`: wheel 127 files (6/6 SQL migrations + typed contract models inside), sdist 190 files (tests + all 8 `contracts/v1/*.schema.json`); fresh-venv installs from **both** wheel `[api]` and `.tar.gz` → `install`/`doctor`/`capabilities`/`mcp` PASS; never PYTHONPATH-dependent.
- [x] CI per §14 — satisfied by suite coverage, no workflow edit needed: `.github/workflows/ci.yml` runs `ruff`, `mypy src`, `pytest -q`, `uv build`, post-install doctor smoke on `windows-latest`+`ubuntu-latest`. Every cycle-10 battery lives **inside** `pytest -q` (tests/contract incl. `test_theforge_adapter.py` adapter conformance, tests/security, tests/conftest.py socket guard = offline marker) — so the CI gate IS the local gate. **Not observed:** hosted run at HEAD (push-triggered) → named as Gate-G condition in `18-release-readiness.md`.
- [x] `docs/cycles/cycle-10/` completed — 14–18 filled (packaging-installation, test-results, compatibility-matrix, unresolved-risks, release-readiness), 00-executive-summary finalized, `evidence/evidence-manifest.json` refreshed wave-5 (sha256 + bytes + truth labels per artifact).
- [x] Acceptance-gate scorecard per §17 — `18-release-readiness.md`: every §17 checkbox item scored with an evidence pointer; gates A/B/C/E/F PASS, D PARTIAL (Bridge/Lens BLOCKED), G PARTIAL (Linux+hosted CI unobserved). Decision: **CONDITIONAL GO** with named conditions.
- [x] Final report §20 A–K — `docs/cycles/cycle-10/final-report.md` (canonical filename per operator direction; all sections real data).
- [x] PRs prepared — **rescoped by operator**: no `gh pr create` (push/PR/tag forbidden this wave). Full drafts + diff stats + merge-order + branch-reality notes in `docs/cycles/cycle-10/15-pr-plan.md` (sentinel `cycle-10`→`main` 9 commits +23 980/−144; portal `cycle-10-sentinel` sentinel-commit `52a21e6` +4 442/−584; the-forge `cycle-10-apisentinel` adapter commit `5aea039` +563/−3). Agent disclosure kept in each body.
- [x] `CHANGELOG.md` cycle-10 entry under `[Unreleased]` (tag plan `v0.1.0-rc.2`, not tagged); `README.md` de-staled (F001 closed — status, real commands, auth note); `docs/16-compatibility-matrix.md` refreshed with homologated versions.

# Constraints

- §19 honored: no merge, no force-push, no release publish, no test weakening, no silent contract changes.
- Every gate checkbox needs an evidence link — no bare checkmarks.
- Decision must be defensible: CONDITIONAL with named conditions beats inflated GO.

# Review Notes

- Cross-check evidence-manifest hashes against real artifacts — DONE:
  `installation-results.json` sha256/bytes recomputed after the wave-5
  rewrites (final: `sha256:9165a833…`, 6 067 B); `capability-manifest.json`
  added with real hash (`8e684097…`). All other entries untouched.
- PR bodies disclose agent authorship per repo norms — DONE: each draft
  in `15-pr-plan.md` carries an "Agent involvement" section.
- Self-review outcome: 8/8 ACs satisfied in-scope; the PR-creation AC
  was deliberately rescoped by the operator (prepare artifacts only —
  no push, no `gh pr create`, no tag). Everything else verified against
  real command output: pytest 544, ruff clean, mypy clean (116 files),
  `uv build` clean, fresh-venv wheel+sdist smoke PASS.
- Deviations from spec text (all defensible, documented):
  `final-report.md` filename instead of `FINAL-REPORT.md` (operator
  direction); capability manifest at `evidence/capability-manifest.json`
  not `contracts/v1/` (would break `test_every_schema_file_covered` —
  noted inside the manifest); no ci.yml edit (all gates already inside
  the pytest suite the workflow runs).

# Grill Gate

- Owner: Edgar
- Out of scope: merging PRs, publishing release, real CI run on GitHub (push triggers it; report the run if observable via gh).
- Riskiest assumption: gh CLI is authenticated for PR creation.
- Smallest acceptable version: docs complete + manifest + gate scorecard + PRs opened.
- _Depends: all previous waves_
