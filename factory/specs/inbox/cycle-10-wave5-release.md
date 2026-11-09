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

- [ ] Install/DX validation per §13: `api-sentinel install` interactive flow (env detect, workspace, profile select, requirements, the-forge/portal detection, plan, approval, verify, receipt); profiles Minimal/Recommended/Full/Headless; portability matrix executed where feasible (PowerShell+Git Bash on Windows; wheel+sdist in fresh venvs; workspace with/without .git; path with spaces; unicode; cp1252; no-TTY; repeat install; uninstall leaves clean-ish state documented) — `evidence/installation-results.json`.
- [ ] Packaging proof: fresh venv + wheel + sdist install → CLI works, migrations packaged, manifests/schemas shipped — never PYTHONPATH-dependent.
- [ ] CI per §14: extend `.github/workflows/ci.yml` gates to include contract tests, security tests, packaging/install-smoke, adapter conformance, offline suite marker — all gates that exist locally must exist in CI (skip only what truly can't run on GHA, documented).
- [ ] `docs/cycles/cycle-10/` completed: fill 14–18 (packaging-installation, test-results, compatibility-matrix, unresolved-risks, release-readiness) + update 00-executive-summary; `evidence/evidence-manifest.json` consolidated {commits, commands, artifacts, sha256, labels} covering all waves.
- [ ] Acceptance-gate scorecard per §17: Gates A–G each item checked with evidence pointer or honest fail — overall decision `GO / CONDITIONAL GO / NO-GO` recorded with rationale in 18-release-readiness.md.
- [ ] Final report in §20 format A–K as `docs/cycles/cycle-10/FINAL-REPORT.md` (or README.md in the cycle dir) — real data only.
- [ ] PRs prepared: `gh pr create` for cycle-10 (sentinel), `cycle-10-api-operations` (forge-portal if changed), `cycle-10-apisentinel` (the-forge if changed) — bodies with summary+test plan per repo conventions; DO NOT merge.
- [ ] `CHANGELOG.md` updated (0.1.0-rc → cycle-10 notes); `README.md` reflects current state (fixes any residual F001); `docs/16-compatibility-matrix.md` refreshed with real verified versions.

# Constraints

- §19 honored: no merge, no force-push, no release publish, no test weakening, no silent contract changes.
- Every gate checkbox needs an evidence link — no bare checkmarks.
- Decision must be defensible: CONDITIONAL with named conditions beats inflated GO.

# Review Notes

- Cross-check evidence-manifest hashes against real artifacts.
- Verify PR bodies disclose agent authorship per repo norms.

# Grill Gate

- Owner: Edgar
- Out of scope: merging PRs, publishing release, real CI run on GitHub (push triggers it; report the run if observable via gh).
- Riskiest assumption: gh CLI is authenticated for PR creation.
- Smallest acceptable version: docs complete + manifest + gate scorecard + PRs opened.
- _Depends: all previous waves_
