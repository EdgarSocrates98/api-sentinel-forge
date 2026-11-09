---
cycle: 10
deliverable: release readiness — §17 acceptance gates A–G, docs/17 checklist re-score, GO/NO-GO decision
wave: 5
---

# Cycle 10 — Release readiness (final gate scorecard)

Scored against `prompt_evo_cycle10.md` §17 item-by-item. Every verdict
cites an evidence file that exists under `docs/cycles/cycle-10/evidence/`
(or a repo path) — nothing is a bare checkmark. Consolidated decision at
the bottom; the §18 DoD items are cross-referenced in `final-report.md`.

Verdict vocabulary: **PASS** (proven by cited evidence) · **PARTIAL**
(proven in part; the unproven remainder is named) · **BLOCKED**
(cannot be proven in this environment; named instructions exist) ·
**FAIL** (would mean evidence contradicts the claim — none).

## Gate A — Core & Security — **PASS**

| §17 item | Verdict | Evidence |
|---|---|---|
| No known local P0 | PASS | `02-revalidated-findings.md` severity roll-up: 0 P0 across F001–F024 |
| Authorization not on untrusted self-declared role | PASS | F007 FIXED — `api/deps.py::_authenticate`; live smoke 401-spoof-dead; `evidence/security-results.json` SEC-API-01…06,08 |
| Cross-workspace access denied | PASS | integrated principal pins `workspace` claim + workspace-id match; `tests/security/test_api_auth.py` |
| Versioned contracts | PASS | `contracts/v1/*.schema.json` (8) + typed envelopes; `tests/contract/` battery incl. unknown-version/truncated negatives |
| Migrations & recovery approved | PASS | `tests/security/test_storage_recovery.py` (12); e2e S16 backup/restore parity |
| Secrets do not leak | PASS | SEC-CLD-13 (0 credential hits in workspace artifacts), SEC-UI-01/02, `token_in_artifacts: false` in `portal-verification.json` |
| CLI & wheel valid | PASS | `evidence/installation-results.json` — wheel+sdist verified, fresh-venv probes PASS |

## Gate B — Doctor & Atlas — **PASS**

| §17 item | Verdict | Evidence |
|---|---|---|
| Doctor receives a real handoff | PASS | `evidence/doctor_inprocess_run.json` — `ForgeRequest` v2 → `DoctorBoundary`, `status: completed` |
| Doctor produces a real result | PASS | same — `ApiHandoffBundle`, 10 findings / 9 unknowns; receipt `evidence/doctor_inprocess_receipt.json` |
| Evidence & unknowns preserved | PASS | verbatim translation at the ACL (findings carry Doctor's own rule ids + STATIC evidence refs) |
| Atlas indexes a repository | PASS | `code-atlas index lab/fixtures/atlas-repo` → snapshot 11 READY — `evidence/atlas/index_run.json` |
| Structural identity resolved when evidence exists | PASS | `evidence/atlas_correlate_run.json` — `service.app#app` → 3 declared CONTAINS edges |
| Ambiguous relations not fabricated | PASS | unresolved `CALLS_ENDPOINT` + 2 external edges stay unresolved/external; no READY snapshot → named refusal (`tests/unit/test_integrations_atlas_store.py`) |
| Negative tests pass | PASS | doctor negatives 14 + atlas-store negatives 10 — `15-test-results.md` |

## Gate C — The Forge — **PASS** (two peer-side items recorded, not blocking)

| §17 item | Verdict | Evidence |
|---|---|---|
| Sentinel discovered | PASS | `theforge registry refresh` → `state=ready` — `evidence/theforge/registry_refresh.json` |
| Capability recognized | PASS | 7 capabilities in live manifest (executor-backed pairs only — F015) |
| Provider eligible | PASS | trusted via user-level config; `evidence/theforge/registry_show.json` |
| Real routing | PASS | `theforge ask` routed to `api-sentinel-forge` provider — `evidence/theforge/runs/*/routing.json` |
| Real execution | PASS | 8/8 declared pairs have executors; 5 routed runs `status=ok` — `08-the-forge-routing.md` |
| Result validated | PASS | each run's `verification.json` `status=passed` (F018 closed) |
| Receipt persisted | PASS | `evidence/theforge/runs/*/receipt.json` (9 run dirs) |
| Provider unavailable handled | PASS | `APISENT-WORKSPACE-UNAVAILABLE` on the scratch probe — honest named refusal, C10-F016 recorded |

Recorded-not-blocking: F016 (conformance probe probes empty workspaces —
peer-side recommendation), F017 (no wire param channel — deterministic
scoping workaround shipped).

## Gate D — Agentic — **PARTIAL** (seams honest; external provider not exercisable)

| §17 item | Verdict | Evidence |
|---|---|---|
| Bridge seam preserved | PASS | governed `AgentSessionRequest` envelope emit, workspace-bounded via `safe_slot` — `09-agent-bridge-and-lens.md` |
| Context authorized | PASS | governance block (principal/workspace/provider/cost/approvals) on every envelope |
| Context pack bounded | PASS | deterministic bounded packs — `api.agentic-investigation` declared |
| Redaction validated | PASS | envelope redacted at write (`bridge/adapter.py`); SEC-AGT battery held |
| Honest states | PASS | `delegated`/`unavailable` named; never promoted |
| External provider tested when available | **BLOCKED** | no `forge_agent_bridge` transport on this host — e2e S12 BLOCKED with operator instructions in `evidence/federated-e2e-receipt.json` |
| Lens traces correlated when applicable | **BLOCKED** | no refs manifest/module — e2e S13 BLOCKED (named `unavailable`) |
| No unauthorized execution | PASS | no execution path exists; nothing ran that wasn't authorized |

Honest verdict: PARTIAL — the two BLOCKED items are named environment
absences, not defects; claiming PASS would fabricate a provider session.

## Gate E — Portal — **PASS**

| §17 item | Verdict | Evidence |
|---|---|---|
| Real Sentinel data displayed | PASS | `evidence/portal-verification.json` — served SPA → live `/v1` (no mocks) |
| Inventory vs runtime separated | PASS | `SentinelApi` dedicated adapter; `PfApi`/`forge/PortalApi/1` absent from the page subtree; PF absent in the e2e harness |
| Incident navigable | PASS | incidents list → detail (ladder + timeline) steps pass |
| Evidence navigable | PASS | evidence metadata + unresolved refs rendered; `/v1/evidence/{id}` metadata-only |
| Governed investigation functional | PASS | real `POST /v1/incidents/{id}/investigations` → Completed |
| Clear error states | PASS | `problem+json` → `ui-error` with verbatim `sentinel.<code>`; `403` → named blocked state |
| Browser E2E approved | PASS | Chromium 19/19 + 7 screenshots; `token_in_artifacts: false` |
| Accessibility validated | PASS | `a11y.spec.ts` — 11 checks green (single h1, `scope=col`, roles, aria-live, keyboard) |
| No fictional buttons | PASS | investigation button scope-gated; every panel renders loading/data/empty/error from real calls |

Deployment note (recorded, not blocking): CSP `connect-src 'self'`
requires the same-origin `/sentinel` proxy topology — C10-F019,
documented + shipped in the e2e harness.

## Gate F — Performance — **PASS**

| §17 item | Verdict | Evidence |
|---|---|---|
| Baseline reproduced | PASS | `evidence/performance-before.json` (verbatim bench + query counters) |
| Bottleneck investigated | PASS | F006 — 10 000 dedup + 10 000 service + ≤2 880 overlap point queries per 10k run |
| Fixes justified | PASS | repository batching (`get_many`/`idempotency_keys`/`overlapping_sources_in_ranges`) — `12-performance-report.md` |
| Before/after comparison | PASS | `performance-after.json` — identical dataset sha256; point queries → constant batches (2 880→2 on dispersed) |
| No correctness regression | PASS | semantic parity all 10 shapes + `tests/integration/test_ingest_batching.py` (5) + 544 suite |
| Memory & throughput documented | PASS | tracemalloc peaks + wall-clock + DB size recorded per shape in both JSONs |
| Retention & recovery preserved | PASS | `tests/security/test_storage_recovery.py`; e2e S16 parity |

Wall-clock is modestly better on dispersion-dominated shapes (−24 %
dispersed, −10 % large_file) and within noise elsewhere — the structural
win is O(n)→O(n/chunk) lookups, honestly scoped in the report.

## Gate G — Release — **PARTIAL**

| §17 item | Verdict | Evidence |
|---|---|---|
| Windows & Linux approved | PARTIAL | Windows: all local gates green (544/ruff/mypy/build/smoke). Linux: `ubuntu-latest` exists in `ci.yml` matrix but no hosted run observed yet |
| Wheel/sdist approved | PASS | contents verified; fresh-venv install smoke PASS — `14-packaging-installation.md` |
| Real CI verified | PARTIAL | `.github/workflows/ci.yml` covers ruff+mypy+pytest+build+install-smoke on win+linux; the cycle-10 batteries (contract/security/adapter-conformance/offline socket-guard) all run *inside* `pytest -q` (tests/contract, tests/security, tests/contract/test_theforge_adapter.py, tests/conftest.py guard) — so the local gate IS the CI gate. **Not observed**: a hosted run at HEAD — first run fires on push/PR |
| Compatibility matrix | PASS | `docs/16-compatibility-matrix.md` + `16-compatibility-matrix.md` (cycle) |
| Evidence versioned | PASS | `evidence/evidence-manifest.json` — sha256 + bytes + truth labels per artifact, wave-5 refreshed |
| Documentation updated | PASS | `docs/cycles/cycle-10/` 00–18 complete; README refreshed (F001 closed); CHANGELOG cycle-10 entry |
| External limitations explicit | PASS | `17-unresolved-risks.md`; named states in `09-agent-bridge-and-lens.md`; `capabilities` stage ceiling |
| GO/NO-GO decision recorded | PASS | this file, §Decision |

## Consolidated view (wave framing)

| Frame | Verdict | One-line proof |
|---|---|---|
| A — reality baseline audited | **PASS** | `evidence/baseline.json` + `baseline-tests.txt` (440-test audit at `29375e3f`) |
| B — contracts + auth hardened | **PASS** | F007/F014/F015/F018/F022/F023 closed; `security-results.json` 40/40 |
| C — real federation proven | **PARTIAL→PASS-named** | Doctor/Atlas/the-forge/Portal **INTEGRATED** with receipts; Bridge/Lens/**api-forge**/**data-sentinel** honestly **NAMED-BLOCKED** — exactly their true state |
| D — Portal real-data UX | **PASS** | `portal-verification.json` 19/19 |
| E — federated E2E | **PARTIAL** | `federated-e2e-receipt.json` — 16 pass / 2 BLOCKED (S12 Bridge, S13 Lens) / 0 fail |
| F — perf + security + AWS | **PASS** | F006 closed (2 880→2); 40/40 held; AWS real bounded (acct hash, 15 calls) |
| G — ops docs + manifest | **PASS** | docs 00–18 + `evidence/evidence-manifest.json` + `evidence/capability-manifest.json` + `15-pr-plan.md` |

## docs/17 checklist re-score

`docs/17-release-readiness.md` is the cycle-1 tracking instrument —
re-scored against current reality:

| # | Item | Score | Note |
|---|---|---|---|
| 1–10 | PRD, ownership, ADRs, identity/metrics/threat/storage/peer-contracts/portal-UX/E2E docs | homologated | docs/00–16 + ADR-001…018 + cycle-10 evidence supersede the cycle-1 state |
| 11 | CI/security/regression gates defined | homologated | `ci.yml` + 544-suite + 40/40 battery; hosted run still pending observation |
| 12 | Explicit AI/cloud limits | homologated | L3 no-LLM core; AWS bounded read-only + budgets; both evidenced |

Release policy check: "operational criteria demonstrated in a
representative environment, not only the local lab" — **partially met**:
the AWS run is a real external but lab-bounded; Bridge/Lens/CI remain
unproven. The enterprise profile is still not marketed as production
readiness — `production_validated` stays unclaimed everywhere.

## Decision

# **CONDITIONAL GO**

The cycle demonstrably delivered its core promise — real cross-forge
federation, a real Portal consumer, hardened auth, proven packaging —
with every unproven item named rather than hidden. A `GO` would require
the items below to already hold; a `NO-GO` would pretend the delivered,
receipted work didn't happen.

**Conditions before tagging `v0.1.0-rc.2`:**

1. **Hosted CI green at HEAD** — push `cycle-10` (or open the PR) and
   observe `windows-latest` + `ubuntu-latest` runs. Local gates mirror
   the workflow exactly, so this is an observation condition, not a
   code one.
2. **PR scope decisions** — `the-forge@cycle-10-apisentinel` and
   `forge-portal@cycle-10-sentinel` both carry unrelated history;
   merge strategy is the owner's call (`15-pr-plan.md` §options).
3. **No fabricated stages** — keep `production_validated` unclaimed;
   the manifest already reflects it.

**Carried, not blocking:** Bridge/Lens named-blocked (optional
integrations, honest states); F016/F017 peer-side recommendations;
F020/F021 P3 docs/surface gaps → cycle-11 candidates.
