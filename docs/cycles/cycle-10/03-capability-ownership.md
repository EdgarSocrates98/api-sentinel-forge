# Cycle 10 — Capability ownership & stage matrix (Wave 0)

Prompt §4 Phase-0 gate: every declared capability gets **exactly its highest
PROVEN stage**. Stages ascend: `IMPLEMENTED → TESTED → INTEGRATED → VERIFIED →
PRODUCTION_VALIDATED`.

## Stage rubric used here

| Stage | Meaning (proof required) |
|---|---|
| IMPLEMENTED | code exists at HEAD |
| TESTED | automated tests cover it at HEAD (440-suite or sibling suite) |
| INTEGRATED | exercised against the real counterpart (real DB/API/peer process — not a file stub or fixture) |
| VERIFIED | proven end-to-end in a real run (e2e smoke / real handoff round-trip) |
| PRODUCTION_VALIDATED | homologated against production-grade externals (real AWS, live peers, hosted CI) |

A capability that emits a file "for a separately-run peer" is **TESTED** at
best — emission is real, consumption unproven. `delegated` ≠ `executed`.

## Local surface

| Capability | Highest proven stage | Evidence |
|---|---|---|
| api.service-inventory | **VERIFIED** | e2e S2 + pytest; boundary executor wired (boundary.py:311) |
| api.telemetry-source-registration | VERIFIED | e2e S3 |
| api.evidence-storage | VERIFIED | pytest + e2e (evidence router, artifact tables) |
| api.persistence-recovery | VERIFIED | e2e S12 backup/restore count parity + S14 restart |
| api.runtime-observation | VERIFIED | e2e S4 ingest + bench; boundary executor wired (boundary.py:336) |
| api.slo-assessment | VERIFIED (CLI) / **TESTED** (forge seam) | e2e S5; forge execute path refuses — F010 |
| api.anomaly-detection | VERIFIED (CLI) / TESTED (forge seam) | e2e S6; F010 |
| api.incident-investigation | VERIFIED (CLI) / TESTED (forge seam) | e2e S7-S8; F010 |
| api.deployment-correlation | VERIFIED (CLI) / TESTED (forge seam) | e2e S9 correlate; F010 |
| api.change-intelligence | VERIFIED | e2e S9 (changes + hypotheses lifecycle) |
| api.portal-operations | **TESTED** | authz matrix + S11 /v1 surface live on loopback; **no portal consumes it** — F005 |
| api.aws-readonly-observability | **TESTED** | AVAILABLE (boto3 importable + proof passes, S15) but **no real AWS account exercised** — F008 |
| api.agentic-investigation | **IMPLEMENTED** | honestly DECLARED (S15); session transport is emit-only — F004 |
| api.doctor-handoff | **TESTED** | emit+ingest verified; **no real Doctor run ever** — F003+F009 |

## Integration surface (cross-forge)

| Seam | Highest proven stage | Evidence |
|---|---|---|
| the-forge provider boundary (`SentinelBoundary` + `theforge_apisentinel` adapter) | **TESTED** | unit tests + manifest/snapshot machinery real; only 2/7 pairs executable — F010; **no real the-forge run routed to it** |
| forge-doctor-api handoff | **TESTED** (file transport only) | emission+receipt proven (S10); in-process path broken at doctor 0.3.1 — F009 |
| forge-agent-bridge session | **IMPLEMENTED→TESTED** | envelope emit + dedup + named unavailable; no execution transport exists — F004 |
| code-atlas structural context | **TESTED** (fixture only) | FileStructuralContext works on lab fixture; **no producer emits the contract** — F011 |
| forge-lens trace refs | **TESTED** | FileLensACL reads exported refs; no lens export homologated |
| data-sentinel handoffs | **TESTED** | handoff_file envelopes; no consumer proven |
| api-forge implementation requests | **TESTED** | handoff_file envelopes; no consumer proven |
| Portal ↔ sentinel /v1 | **IMPLEMENTED** | sentinel API exists + tested; **portal wired to product-forge** — F005 |
| `api-sentinel mcp serve` | **ABSENT** | manifest declares it, command does not exist — F012 |

## PRODUCTION_VALIDATED candidates

**None.** Every federated claim tops out at TESTED/IMPLEMENTED; external
homologations remain blocked (F008). Gate D items (real AWS, live peers,
hosted CI, portal E2E) are all unproven at Wave 0.

## Ownership

| Domain | Owner | Note |
|---|---|---|
| Sentinel core + contracts | sentinel maintainer | stable |
| Doctor seam | sentinel + doctor maintainers | fix F009, then homologate |
| Bridge seam | sentinel + bridge maintainers | define consumption contract |
| Atlas contract | code-atlas + sentinel | producer missing — F011 |
| Portal surface | portal + sentinel + product-forge | adapter needed — F005 |
| Forge routing | the-forge + sentinel | executor gap — F010 |

---

## Wave-5 refresh (final stage state — supersedes the tables above)

Later waves proved more than the Wave-0 audit recorded. Final stages,
as served by `api-sentinel capabilities` / `/v1/capabilities` at HEAD
(`src/api_sentinel/capabilities.py`), captured verbatim in
`evidence/capability-manifest.json`:

| Capability | Final stage | Change driver |
|---|---|---|
| api.portal-operations | TESTED → **VERIFIED** | Wave 3 — real Portal consumer: served SPA + same-origin proxy + Chromium 19/19 (`evidence/portal-verification.json`, F005 closed) |
| api.aws-readonly-observability | TESTED → **VERIFIED** | Wave 4 — bounded real AWS account homologation (`evidence/aws-validation.json`, F008 partially closed) |
| api.doctor-handoff | TESTED → **VERIFIED** | Wave 2 — real in-process Doctor 0.3.1 run (F009 closed) |
| the-forge seam (8 pairs) | TESTED → **VERIFIED** | Wave 2 — routed `theforge ask` runs with receipts (F010/F015/F018 closed) |
| code-atlas seam | TESTED (fixture) → **VERIFIED** (real store) | Wave 2 — `DbStructuralContext` over real `atlas.db` snapshot (F011 closed) |
| `api-sentinel mcp serve` | ABSENT → **VERIFIED** | Wave 1 — read-only stdio server implemented (F012 closed) |
| api.agentic-investigation | stays **IMPLEMENTED/DECLARED** | Bridge transport still absent (F004; S12 honestly BLOCKED) |

**PRODUCTION_VALIDATED: still none.** The AWS run was real but bounded
(single homologation account, demo API, ≤15 calls) — a lab-grade
external, not a production workload.
