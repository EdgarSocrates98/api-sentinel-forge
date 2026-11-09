---
id: cycle-10-wave2-federation
title: "Cycle 10 Wave 2 — Federated integration closure (Doctor/Atlas/TheForge/Bridge/Lens)"
agent: claude
risk: high
verification:
  - "python -m pytest -q"
  - "python -m ruff check ."
  - "python -m mypy src"
---

# Context

Implements prompt_evo_cycle10.md §6 (Phase 2) on `cycle-10` branch. INPUTS: Wave-0 findings + contract inventory (`docs/cycles/cycle-10/04-contract-compatibility.md`), `docs/06-integration-contracts.md`, existing `src/api_sentinel/integrations/` + `adapters/apisentinel/` (the-forge adapter skeleton from Cycle 5). Sibling repos at `E:\projetos\FORJAS\` — real installs may be needed in their own venvs (uv), changes to siblings only on dedicated branches with separate commits.

# Acceptance Criteria

- [ ] **Doctor real consumption (Gate B):** Sentinel handoff → real `forge-doctor-api` execution locally (install its package into a venv or invoke its boundary per its AGENTS.md — check `src/forge_doctor_api/handoff/` for the supported entrypoint), real findings+unknowns returned, associated to the incident with provenance preserved, receipt persisted, result visible via `/v1/incidents/{id}` + investigation. Negative tests: doctor absent, version mismatch, invalid OpenAPI, truncated handoff, timeout, nonzero exit, malformed result, unresolved ref, path outside workspace.
- [ ] **Atlas real indexing (Gate B):** code-atlas-forge actually indexes a lab repo (its real CLI per its repo docs), snapshot produced, Sentinel resolves service↔structural entity via its real seam; edges classified structural/observed/inferred/unresolved with explicit resolution class; no name-only binding; ambiguous → not fabricated. Negative tests cover §6.3 list.
- [ ] **The Forge real registration (Gate C):** `adapters/apisentinel/` copied into `the-forge` repo on branch `cycle-10-apisentinel` + provider registered per its onboarding path (check `docs/real-providers.md`, `forge-knowledge`); prove `discover → route → execute → validate → receipt` against the real the-forge runtime for ≥1 capability; provider-unavailable handled honestly; commit in the-forge repo on ITS branch (not main).
- [ ] **Bridge governed seam (Gate D):** context pack + session request flow preserved; `delegated` never reported as `executed`; if no authenticated provider exists → states BLOCKED/NOT_RUN recorded, never fake session. If forge-agent-bridge has a runnable local seam, wire it for real session-open with principal/cost/approval preserved + receipt.
- [ ] **Lens refs:** correlation by session_id/trace_id/correlation_id/workspace/provenance only — never a second trace store; absent → named UNAVAILABLE.
- [ ] **API Forge handoff:** implementation-request envelope per existing pattern + response-ingestion path documented; real consumption optional (gate says optional).
- [ ] **Data Sentinel:** only domain-relevant scenario (stale-data correlation) or marked UNEXERCISED-OPTIONAL honestly — never HTTP logs to a Spark analyzer.
- [ ] Docs: `docs/cycles/cycle-10/{06-doctor-integration,07-code-atlas-integration,08-the-forge-routing,09-agent-bridge-and-lens}.md` + `evidence/contract-conformance.json` + handoff receipts under evidence/ with real content.
- [ ] Capabilities updated to reflect REAL proven stages only.

# Constraints

- No duplication: translate/adapt, never reimplement foreign engines.
- Every foreign result enters via ACL with version check; unknowns preserved verbatim.
- Cross-workspace access denied; handoff payloads bounded+redacted.
- the-forge changes: dedicated branch, minimal, justified; NEVER modify the-forge just to accommodate an invalid manifest.
- Do not merge sibling branches — push branches + open PRs at Wave 5.

# Review Notes

- Confirm a REAL doctor process ran (pid/command in receipt), not just file emission.
- Confirm the-forge routing reached the sentinel adapter end-to-end.
- Confirm delegated≠executed distinction everywhere.

# Grill Gate

- Owner: Edgar
- Out of scope: agent-provider authenticated sessions without creds, Portal UI (Wave 3), AWS real (Wave 4/6).
- Riskiest assumption: sibling CLIs/boundaries are consumable as documented.
- Smallest acceptable version: Doctor real + the-forge discover→execute→receipt; Atlas if its seam is consumable; others honestly BLOCKED.
- _Depends: cycle-10-wave1-hardening (auth may affect handoff security)_ — sequential anyway.
