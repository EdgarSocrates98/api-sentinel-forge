---
id: cycle-10-wave0-baseline
title: "Cycle 10 Wave 0 — Reality audit, baseline & findings"
agent: claude
risk: medium
verification:
  - "python -m pytest -q"
  - "python lab/e2e_validate.py --smoke 2>/dev/null || true"
---

# Context

Cycle 10 (prompt_evo_cycle10.md) requires a reality audit BEFORE any change: revalidate all repo mains, register baseline, reproduce known findings C10-F001..F008, and produce the capability stage matrix `IMPLEMENTED → TESTED → INTEGRATED → VERIFIED → PRODUCTION_VALIDATED`. Work happens on the `cycle-10` branch. This wave writes ONLY docs/evidence — no product code.

# Acceptance Criteria

- [x] `docs/cycles/cycle-10/evidence/baseline.json`: for each of the 9 repos (api-sentinel-forge, the-forge, forge-doctor-api, api-forge, code-atlas-forge, forge-agent-bridge, forge-lens, data-sentinel-forge, forge-portal, product-forge): default branch, HEAD sha, package version, contract/protocol versions found, working-tree state, relevant PRs/branches, install state. *(Wave 0: all 10 enumerated repos covered — real SHAs, gh-authenticated PR lists, venv/module probes.)*
- [x] Sentinel test suite re-run: `pytest -q` output captured into `docs/cycles/cycle-10/evidence/baseline-tests.txt`; ruff+mypy tails captured too. *(440 passed/58.45s; ruff 2 errors in scripts/cov_core.py; mypy clean 112 files; e2e smoke 15/15 also captured.)*
- [x] All 8 known findings (C10-F001..F008 in prompt_evo_cycle10.md §1.2) reproduced or refuted at current HEAD with evidence: README freshness, cli:app vs cli:main wheel check, handoff-consumption gap, bridge delegated≠executed, portal↔sentinel link, ingestion perf bottleneck (overlap-source query amplification — run bench and inspect query counts), X-Sentinel-Role trust, external homologation state. *(F002 refuted via clean-wheel install; all others confirmed with file:line + command-output evidence; 5 new findings F009–F013 surfaced.)*
- [x] `docs/cycles/cycle-10/02-revalidated-findings.md`: finding ledger in the prompt's format (id/severity/repository/file/symbol/contract/root_cause/reproduction/impact/evidence/owner/remediation/negative_tests/compatibility/status), classified P0–P3 honestly. *(0×P0, 9×P1, 1×P2, 2×P3.)*
- [x] `docs/cycles/cycle-10/01-repository-baseline.md` + `docs/cycles/cycle-10/03-capability-ownership.md`: capability stage matrix — every declared capability gets exactly its highest PROVEN stage (IMPLEMENTED/TESTED/INTEGRATED/VERIFIED/PRODUCTION_VALIDATED) with the evidence pointer.
- [x] `docs/cycles/cycle-10/00-executive-summary.md` seeded with baseline facts (final sections filled later waves).
- [x] Cross-repo contract inventory in `docs/cycles/cycle-10/04-contract-compatibility.md`: real names/versions of ForgeRequest/ForgeHandoff/ExecutionResult in the-forge, ApiHandoffBundle in forge-doctor-api, PortalApi envelope in product-forge, Atlas export seams — read actual source, quote file:line.

# Constraints

- Do NOT modify any sibling repo; read-only audit there.
- Do NOT modify Sentinel product code this wave.
- Evidence JSONs must contain real command outputs/hashes.
- If a repo lacks an installed dev env, record that — do not install without need.
- Findings are evidence-grounded: severity from actual reproduction, not speculation.

# Review Notes

- Check that P0 is not inflated (unavailable optional integration ≠ P0).
- Check the stage matrix has one stage per capability with evidence links.
- Check baseline.json covers ALL listed repos with real SHAs.

# Grill Gate

- Owner: Edgar (repo owner)
- Out of scope: fixing findings (Waves 1+), sibling repo modifications, AWS creds.
- Riskiest assumption: sibling repos' main branches are buildable/installable locally.
- Smallest acceptable version: baseline + findings ledger + stage matrix.
