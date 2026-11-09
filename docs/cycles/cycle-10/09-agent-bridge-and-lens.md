---
cycle: 10
deliverable: Agent Bridge + Lens + API Forge + Data Sentinel — named honest states
wave: 2
---

# Cycle 10 — Bridge / Lens / API Forge / Data Sentinel (Wave 2)

Recon performed against the sibling checkouts; `api-sentinel integrations`
against the lab workspace reports the honest table:

| Forge | State | Mode | Reality |
|---|---|---|---|
| forge-agent-bridge | `unverified` | `file` | `forge_agent_bridge` not importable in the Sentinel venv — governed session requests emit `agent_sessions/<id>/session_request.json` envelopes for a separately-run Bridge; **delegated, never executed** |
| forge-lens | `unavailable` | `file` | `forge_lens` not importable and no `integrations.lens.refs` configured — trace references need an exported refs manifest; the Sentinel never builds a second trace store |
| api-forge | `unverified` | `handoff_file` | `apiforge` not importable — implementation-request envelopes emit under `handoffs/` for a separately-run peer; the Sentinel never edits API Forge's code |
| data-sentinel-forge | `unverified` | `handoff_file` | `data_sentinel` not importable — only domain-relevant stale-data handoffs emit envelopes; generic HTTP telemetry never leaves |

## Bridge — governed seam, no fake session

`open_session` emits the governed `AgentSessionRequest` envelope
(redacted, workspace-bounded via `safe_slot`, governance block preserved:
principal, workspace, provider, cost limit, approvals) and reports
`STATUS_DELEGATED`. The Bridge owns providers/approvals/budgets/
execution — nothing is claimed executed. With no authenticated provider
transport the result is `unavailable`/`delegated`, never a fabricated
session. (Peer-side session-import seam: none found in
`forge-agent-bridge/src` — the file envelope remains the only honest
transport until the Bridge adds an intake command.)

## Lens — reference-only correlation

`FileLensACL` reads an exported refs manifest
(`integrations.lens.refs`): `{refs: [{trace_id, session_id?, at?,
service_id?, provenance?}]}`; unknown keys are dropped by an allowlist —
payloads are cited, never ingested. Correlation keys stay
`session_id`/`trace_id`/`correlation_id`/workspace/provenance only. No
manifest configured → `unavailable` (named), which is the current lab
state.

## API Forge — handoff envelope path documented

`integrations/apiforge/adapter.py` emits implementation-request envelopes
under `handoffs/` (VERIFIED-hypothesis gate); real consumption happens when
a separately-run API Forge reads them. The in-process path activates only
when `apiforge` is importable — named `unverified` until confirmed, as
now.

## Data Sentinel — `UNEXERCISED-OPTIONAL` honest state

Only domain-relevant stale-data scenarios may leave; generic HTTP logs are
never routed to a Spark analyzer. With `data_sentinel` not importable the
state is `unverified | handoff_file` — envelopes under `handoffs/` for a
separately-run peer. No generic telemetry crossed the wire this cycle.

## What is NOT claimed

No real Bridge session was opened, no Lens trace was consumed, no API
Forge implementation request was executed, and no Data Sentinel analysis
ran — all four are named states with the exact transport described above,
not simulated success. (Spec's smallest-acceptable bar: Doctor real +
the-forge real — both delivered; Atlas real — delivered.)
