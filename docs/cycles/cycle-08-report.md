# Cycle 8 — Agentic Investigation & Change Intelligence

Extends the deterministic Sentinel with a *governed seam* for agentic
investigation (ADR-010/016) and a change-intelligence lane: `ChangeEvent`
coverage for commits/releases/deploys/rollbacks/flags/config/gateway/
auth-policy/contract/dependency/maintenance, exported
`api-sentinel/DeployManifest/v1` ingestion (no GitHub credentials),
honest temporal correlation that never claims causality, an explainable
hypothesis lifecycle enforced on evidence, typed verification plans and
independent post-fix verification, Lens references by opaque id,
Data Sentinel staleness probes, and a versioned API Forge
implementation-request handoff — every sibling Forge behind an
anti-corruption adapter with named degradation, never simulated success.

## Verification tails

```text
$ python -m pytest -q
365 passed in 17.85s

$ python -m ruff check .
All checks passed!

$ python -m mypy src
Success: no issues found in 108 source files
```

### LAB-004 CLI smoke (real CLI, temp workspace)

```text
$ api-sentinel changes import --manifest lab-004/deploy-manifest.json --json
{"imported": 2, "deduplicated": 0, "skipped": 0}

$ api-sentinel ingest --file lab-004/observations.jsonl --json
{"accepted": 18, "quarantined": 0, "skipped_duplicates": 0}

# detect → 2 incidents (error-ratio + source-silence); correlate on the
# error-ratio incident:
$ api-sentinel changes correlate --incident-id inc_39da… --json
candidates[0]: chg_e55e… deploy checkout-api 2.7.0 sha_a11ce99
               score=1.0 delta=+30.0s
unknowns:      ["post-anchor change chg_26df… excluded from candidates"]
disclaimer:    "proximity≠cause: temporal proximity alone is not root
                cause — independent evidence is required before any
                causal claim"

$ api-sentinel investigate --incident-id inc_39da… --json
{"context_pack_id": "pack_09ec…", "pack_bytes": 4129,
 "truncated_items": [], "session_status": "delegated",
 "session_request_id": "asr_897f…", "status": "COMPLETED"}

# governed envelope on disk — principal/workspace/provider/cost/approvals
$ cat …/agent_sessions/asr_897f…/session_request.json
{principal: "edgar", workspace: "local", provider: "claude",
 cost_limit_usd: 2.0, approvals: ["read:evidence"]}

$ api-sentinel hypotheses add --incident-id inc_39da… \
    --text "deploy sha_a11ce99 (2.7.0) introduced the 503 regression" \
    --confidence CORRELATED --evidence-for chg_e55e… \
    --unknown "independent post-fix verification pending" --json
{"hypothesis_id": "hyp_6ba1…", "confidence": "CORRELATED"}

$ api-sentinel hypotheses verify --incident-id inc_39da… \
    --hypothesis-id hyp_6ba1… --window-start 2026-01-01T00:10:00Z \
    --window-end 2026-01-01T00:12:00Z --json
{"verdict": "VERIFIED", "verification_id": "ver_4b93…",
 "receipts": [{"kind": "re_detect", "status": "supporting",
   "detail": "6 rule(s) evaluated clean on post-rollback window"}]}

$ api-sentinel hypotheses promote --incident-id inc_39da… \
    --hypothesis-id hyp_6ba1… --verification-id ver_4b93… --json
{"confidence": "VERIFIED"}            # re-promote → named refusal

$ api-sentinel handoff apiforge --incident-id inc_39da… \
    --hypothesis-id hyp_6ba1… \
    --suggested-change "revert checkout-api 2.7.0 to 2.6.3" --json
{"handoff_id": "hnd_af_2221…", "status": "delegated",
 "request_ref": "…/handoffs/hnd_af_…/implementation_request.json"}
```

## Acceptance criteria

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Agentic sessions governed by the Agent Bridge; no Sentinel runtime; principal/workspace/provider/cost/approvals/history preserved | ✅ | `investigation/agent_session.py` `AgentSessionPort`; `integrations/bridge/` `FileAgentSession` writes versioned `api-sentinel/AgentSessionRequest/v1` envelopes under `agent_sessions/`; `SessionGovernance` carries all fields end-to-end (CLI smoke above); the Sentinel never executes prompts |
| 2 | Lens traces by id + provenance, never full copies | ✅ | `LensTraceRef` = `trace_id`+`session_id`+`provenance`; `integrations/lens/` ACL allowlist drops any trace payload field (`test_lens_*`); context packs carry refs only |
| 3 | Context packs bounded; logs/traces/docs never become instructions | ✅ | `context_pack.py` — `PackBudget` byte cap + per-item excerpt cap, mechanical `role="data"` fence, `instructions` structurally separate section, redact-before-pack, truncation named in `unknowns`; `test_pack_*` incl. injection attempts |
| 4 | `ChangeEvent` covers all 11 kinds; deploy manifests without GitHub creds | ✅ | `ChangeKind` extended (`commit`,`release`,`gateway`,`auth_policy` added); `DeployManifestV1` + `contracts/v1/deploy_manifest.schema.json`; `changes record/import` dedup by deterministic change id |
| 5 | Configurable-window correlation + plausible impact + independent evidence; LAB-004; proximity never cause | ✅ | `changes.correlate_changes` — `--window` flag, score is `proximity`+`kind`+`service-match` with per-candidate `reasons`, post-anchor changes excluded-and-named in `unknowns`, `PROXIMITY_DISCLAIMER` on every report; LAB-004 fixture + e2e test |
| 6 | Explainable hypotheses; no fabricated numeric confidence | ✅ | `Hypothesis` carries `evidence_for`/`evidence_against`/`unknowns`; `ConfidenceClass` ordinal ladder, distinct from `IncidentStatus`; no float confidence anywhere in the model |
| 7 | `OBSERVED → CORRELATED → HYPOTHESIZED → VERIFIED` enforced | ✅ | `hypotheses.py` — add only at `CORRELATED`/`HYPOTHESIZED`; `promote` requires persisted `VerificationRecord` with verdict `VERIFIED` matching hypothesis+incident; `REFUTED` only via `demote` with mandatory reason; journaled (`HYPOTHESIS_*`,`VERIFICATION_RECORDED`) |
| 8 | Verification plans bound to authorized tools with stated scope | ✅ | `VerificationPlan`/`VerificationStep` — each step names `check`, `tool`, `expected_signal`, `falsification`, `scope`; unknown tools refuse pre-execution (`test_verification_*`) |
| 9 | External actions need authorized scope + explicit approval | ✅ | `SessionGovernance.approvals` propagates to the Bridge envelope; handoff constraints carry `scopes`; the Sentinel exposes **no** agent-executed action path — agent output is data, actions exist only via the governed session/handoff seam |
| 10 | API Forge handoff = problem + evidence + suggested change via governed path; never edits code | ✅ | `integrations/apiforge/` ACL emits `api-sentinel/ApiForgeImplementationRequest/v1` envelopes to `handoffs/` (VERIFIED hypotheses only); response returns via `handoff ingest`; zero writes to api-forge |
| 11 | Independent post-fix verification; resolution never self-declared | ✅ | `verification.py` `re_detect` re-runs the same rules on a post-change window — VERIFIED only on clean re-fire check; promotion to `IncidentStatus.RESOLVED` stays separate and journaled |
| 12 | Bridge unavailable → deterministic investigations continue; named degradation | ✅ | `UnavailableAgentSession` returns `status="unavailable"` named state; investigation + pack persist regardless; `test_*_unavailable` covers Bridge/Lens/Data Sentinel/API Forge seams |

## Changed files

- **Investigation core (new):** `src/api_sentinel/investigation/` —
  `context_pack` (bounded/redacted/fenced packs), `agent_session` (port
  + governance + request/result models), `hypotheses` (lifecycle
  enforcement), `verification` (typed plans, step receipts, verdicts),
  `changes` (`ChangeEvent` models, manifest import, proximity
  correlation), `service` (orchestrator use cases).
- **Integration ACLs (new):** `integrations/bridge/` (file-backed
  session seam + `UnavailableAgentSession`), `integrations/lens/`
  (trace refs only), `integrations/data_sentinel/` (staleness probe,
  domain-payload validator), `integrations/apiforge/` (request emit /
  response ingest); `integrations/status.py` reports every seam
  honestly (`unavailable` named, never simulated).
- **Domain/contracts:** `incidents/models.py` — `REFUTED` confidence,
  directional evidence fields, `verification_id`, `VerificationRecord`;
  `incidents/journal.py` — `VERIFICATION_RECORDED`, `SESSION_REQUESTED`;
  `contracts/v1/models.py` + `__init__.py` — `DeployManifestV1` and
  handoff/session envelopes; `contracts/v1/deploy_manifest.schema.json`.
- **Surface:** `cli/__init__.py` — `investigate`, `hypotheses
  add/promote/demote/verify`, `changes list/record/import/correlate`,
  `handoff apiforge/ingest`; `capabilities.py` —
  `api.change-intelligence`, `api.agentic-investigation`;
  `workspace.py` — `agent_sessions_dir`; `application/use_cases.py` —
  timeline gains investigations/hypotheses/verifications/changes.
- **Lab/docs/spec/tests:** `lab/fixtures/lab-004-regressive-deploy/`
  (manifest + 18 observations + README + services seed); this report +
  ticked spec; `tests/unit/test_investigation.py` (pack bounds,
  redaction, injection fence, lifecycle, verification guards),
  `tests/integration/test_cycle8_agentic.py` (LAB-004 e2e,
  degradation, dedup, timeline), `tests/contract/test_contracts.py`
  (8th envelope).

## Mismatches & honest gaps

- **The Agent Bridge here is a file-envelope seam** — the real
  `forge-agent-bridge` repo consumes `session_request.json` out-of-band
  and returns results via the same directory; no in-process provider
  call exists by design (ADR-010).
- **Correlation scoring is deliberately simple** (temporal delta +
  change-kind prior + service-name match); it ranks, it does not
  conclude — the disclaimer is part of every payload, not a UI nicety.
- **Lens/Data Sentinel adapters are reference + probe only** — the
  Sentinel cannot pull traces or Sentinel findings; that is the
  documented ADR-016 posture, not missing code.
- **Timeline starts at `first_observed_at`** — the *pre*-anchor deploy
  is visible through `changes correlate` (where it ranked #1), not the
  incident timeline; tested as intended behavior.
- **`handoff apiforge` accepts only VERIFIED hypotheses** — lower
  confidence produces a named refusal; this matches "problem + evidence
  + suggested change" being a *delegation*, not a suggestion box.

Commit: see `git log -1` (`Cycle 8: agentic investigation & change
intelligence`).
