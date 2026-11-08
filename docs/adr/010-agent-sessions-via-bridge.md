# ADR-010 — Agentic Sessions via Agent Bridge

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

Level-3 agentic investigation needs agent sessions. The Agent Bridge
already owns session creation and communication with agents; building a
second runtime duplicates governance and safety controls.

## Decision Drivers

- Sessions must preserve authorized principal, workspace, provider
  selection, cost limits, approvals and history — all Bridge concerns.
- Logs/traces/docs are untrusted input, never agent instructions —
  enforcement belongs to the session governor, not each caller.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Sessions created and governed by Agent Bridge | Sentinel requests an investigation session with bounded context pack | reuses approval/cost/principal governance |
| B — Sentinel's own agent runtime | embedded model calls | duplicates session governance; new prompt-injection surface to secure alone |

## Decision

The Sentinel may initiate an agentic investigation, but the session is
created and governed by the Agent Bridge. Cycle 8 scope: bounded
context packs, explainable hypotheses, verification plans, action
approval, handoff to api-forge, independent post-fix verification.

## Consequences

**Positive**

- No credentials-for-agents in the Sentinel core.
- Bridge-unavailable degrades to deterministic investigation only
  (docs/02 failure strategy).

**Negative**

- Agentic features blocked until Cycle 8 and Bridge availability.
- Context-pack design must resist prompt injection from observed data.

## Compatibility

Sentinel ↔ Bridge exchanges are adapter-mediated; investigation records
store session ids + provenance, not agent internals.

## Reversal Strategy

If Bridge is retired or replaced, the integration point is the
`forge-agent-bridge` adapter behind the investigation port — the
incident model keeps hypotheses/verifications regardless of which
agent runtime produced them.

## Links

- Blueprint `prompt_inicial.md` §13.6, §16.1 (prompt injection), §23
  (ADR-010)
- `docs/06-integration-contracts.md`, `docs/08-slo-and-detection-design.md`
