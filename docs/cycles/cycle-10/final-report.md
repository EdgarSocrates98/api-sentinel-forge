---
cycle: 10
deliverable: final consolidated report — §20 format A–K
wave: 5
---

# Cycle 10 — Final Report

Cross-forge integration, operational UX & production readiness —
executed in 6 waves on `cycle-10`. Format per `prompt_evo_cycle10.md`
§20. All numbers cite receipts under `docs/cycles/cycle-10/evidence/`.

## A. Executive Summary

**Result: CONDITIONAL GO** (`18-release-readiness.md`).

Cycle 10 converted the Sentinel's federated surface from
audited-but-unproven into receipted reality. At Wave 0 every peer seam
topped out at file-emission; at close, **Doctor, Code Atlas, the-forge
and the Portal are INTEGRATED with receipts**, and the four remaining
seams are honestly named blocked/unverified — never simulated.

- **Advanced**: real `/v1` auth (bearer + HMAC principal); real Doctor
  in-process run; real Atlas store seam; the-forge provider with 8/8
  execute pairs routed + receipted; Portal `/api-operations` on live
  Sentinel data (Chromium 19/19); federated golden path 16/18 + 2
  honestly blocked; ingestion N+1 closed; bounded real AWS homologation;
  read-only MCP server; packaging + install re-verified.
- **Corrected**: 13 findings fixed (F001, F005–F007, F009–F015, F018,
  F022, F023), 1 refuted (F002), the rest recorded with honest states.
- **Remaining risks**: hosted CI not yet observed at HEAD; Bridge/Lens
  transports absent (named); the-forge/portal PR branches carry
  unrelated history (owner decides merge strategy); no capability is
  `production_validated`.
- **Status**: `CONDITIONAL GO` — conditions listed in
  `18-release-readiness.md` §Decision (hosted CI green, PR scope calls,
  keep stages honest).

## B. Baseline

| Repo | Cycle-10 HEAD / state | Version |
|---|---|---|
| **api-sentinel-forge** | `cycle-10` — wave 0 audited at `29375e3f`; wave-5 HEAD (this commit) | `0.1.0` (rc line) |
| forge-doctor-api | `main` @ `fe99013` — editable-installed in sentinel venv for the real run | `0.3.1` |
| code-atlas-forge | `main` @ `12bdc39` — real `atlas.db` (snapshot 11) consumed read-only | `1.1.1` |
| the-forge | `cycle-10-apisentinel` — adapter commit `5aea039` (+phase-2 history) | `0.7.4` |
| forge-portal | `cycle-10-sentinel` — sentinel commit `52a21e6` on `1e85c28`-era base | `1.1.1` |
| forge-agent-bridge | `fix/phase1.1…` @ `92df03f` — audited only | `0.2.1` |
| forge-lens | `main` @ `5687788` — audited only | `0.2.1` |
| api-forge | `fix/control-plane…` @ `029b9aa` — audited only | `0.3.2` |
| data-sentinel-forge | `main` @ `e222af8` — audited only | `0.5.2` |
| product-forge | `fix/bridge-embedded…` @ `2660d39` — audited; deliberately independent | `0.2.2` |

Environment: Windows 11 host, Python **3.12.13** (dev + smoke venvs),
uv-managed; sentinel baseline at audit: 440 tests, 15/15 e2e smoke.
Contracts exercised: `forge/v1`, `theforge/ExecutionResult/v1`,
`ForgeRequest` v2 / `ApiHandoffBundle` v1|v2, Atlas SQLite store,
`api-sentinel/*/v1` family, `application/problem+json`,
`forge/SpecialistAgenticManifest/v1`. Machine-readable baseline:
`evidence/baseline.json`.

## C. Findings (final ledger — all 24)

| Id | Sev | Final state |
|---|---|---|
| F001 README stale | P3 | **FIXED (W5)** — status line + commands + spec range refreshed |
| F002 cli:main vs cli:app | — | **REFUTED** — two valid contracts |
| F003 Doctor handoffs unconsumed | P1 | **CLOSED (W2)** — real in-process run completes via F009 fix; `handoff_file` mode stays honest `delegated` |
| F004 Bridge delegated≠executed | P1 | **OPEN-named** — no session transport exists bridge-side; `unverified\|file`, e2e S12 BLOCKED |
| F005 Portal off Sentinel | P1 | **FIXED (W3)** — `SentinelApi` + same-origin proxy, 19/19 browser proof |
| F006 ingestion N+1 | P2 | **FIXED (W4)** — batch ports; 2 880→2 overlap queries dispersed; parity all shapes |
| F007 self-declared role | P1 | **FIXED (W1)** — bearer + HMAC principal; spoof → 401 |
| F008 external homologations blocked | P1 | **PARTIAL (W4)** — real bounded AWS done; live-peer + hosted-CI items remain |
| F009 stale ProjectContext import | P1 | **FIXED (W2)** — `core.context` + legacy fallback |
| F010 5/7 executor pairs refuse | P1 | **FIXED (W2)** — 8/8 pairs execute through real runtime |
| F011 AtlasExport orphan | P1 | **FIXED (W2)** — `DbStructuralContext` over real `atlas.db` |
| F012 mcp_command unresolvable | P1 | **FIXED (W1)** — read-only stdio server shipped |
| F013 ruff debt | P3 | **FIXED (W2)** — clean |
| F014 requiredness asymmetry | P3 | **FIXED (W2)** — model = schema (`payload`/`constraints` required) |
| F015 manifest `default_action: null` | P1 | **FIXED (W2)** — only executor-backed pairs declared |
| F016 conformance probes empty ws | P2 | **RECORDED** — honest `APISENT-WORKSPACE-UNAVAILABLE`; peer-side probe-tier recommendation |
| F017 forge/v1 no param channel | P2 | **WORKED AROUND** — explicit → single-service → named ambiguity |
| F018 dangling evidence refs | P1 | **FIXED (W2)** — refs re-emitted as declared Evidence |
| F019 CSP `connect-src 'self'` | P2 | **DOCUMENTED + WORKED AROUND** — same-origin `/sentinel` proxy pattern shipped |
| F020 serve-port doc drift | P3 | **OPEN** — 8317 is the operative default; one-place fix recommended |
| F021 source error/retry fields | P3 | **OPEN** — additive projection candidate; UI flags not-exposed |
| F022 bundle ingest too permissive | P2 | **FIXED (W4)** — `handoff_version` required |
| F023 demo exporter illegal field | P3 | **FIXED (W4)** — contract-legal export |
| F024 `login_session` unresolvable | P3 | **DOCUMENTED** — `botocore[crt]` or env-export path |

Roll-up: **0 P0** · 11 P1 → 9 fixed + F004 open-named + F008 partial ·
5 P2 → 3 fixed + 2 recorded/worked-around · 7 P3 → 4 fixed + 3
open/documented · 1 refuted. Severity rubric + per-finding evidence in
`02-revalidated-findings.md`.

## D. Changes by Repository

| Repo | Branch | Commits | Diff | Contents |
|---|---|---|---|---|
| api-sentinel-forge | `cycle-10` | 10 (9 waves + this wave-5) | 213 files +23 980/−144 (+ wave-5) | auth, batching, atlas store adapter, MCP server, boundary executors, capabilities refresh, docs 00–18 + evidence, lab runners |
| forge-portal | `cycle-10-sentinel` | 1 sentinel (`52a21e6`; 21 unrelated on branch) | 23 files +4 442/−584 | `src/app/sentinel/*`, page rewrite, i18n, e2e harness + screenshots |
| the-forge | `cycle-10-apisentinel` | 1 sentinel (`5aea039`; ~89 unrelated) | 9 files +563/−3 | `adapters/apisentinel/` distribution + mypy config |
| others | — | 0 | — | audit-only (doctor/atlas/lens/apiforge/datasentinel/product-forge untouched) |

PRs: **prepared, not opened** — `15-pr-plan.md`. Migrations: none added
(6 remain; storage changes were query-level, not schema). Contract
changes: wire-compatible only (F014 reader-side alignment; F015/F018
declaration/result fixes) — all recorded, none silent.

## E. Cross-Forge Integration

| Forge | Final state | Proof |
|---|---|---|
| forge-doctor-api | **INTEGRATED** | in-process `DoctorBoundary` run completed; `evidence/doctor_inprocess_run.json` + receipt `rcpt_hnd_h_01M70777F9VS1TQ2XNJ8ZD5X8Q`; 10 findings / 9 unknowns ingested |
| code-atlas-forge | **INTEGRATED** | real index (snapshot 11) + correlate → 3 declared CONTAINS edges; `evidence/atlas*/` |
| the-forge | **INTEGRATED** | `state=ready` provider, 8/8 pairs, 5 routed runs `status=ok` + `verification=passed` + receipts — `evidence/theforge/` |
| forge-portal | **INTEGRATED** | `portal-verification.json` — 19/19, PF absent, token never in DOM |
| forge-agent-bridge | **NAMED `unverified\|file`** | no session transport; governed envelopes only — e2e S12 BLOCKED |
| forge-lens | **NAMED `unavailable\|file`** | no refs manifest — e2e S13 BLOCKED |
| api-forge | **NAMED `unverified\|handoff_file`** | emit-only |
| data-sentinel-forge | **NAMED `unverified\|handoff_file`** | stale-data only; unexercised-optional |
| product-forge | **independent by design** | portal surface proven with PF absent |
| AWS | **EXTERNAL_REAL (bounded)** | STS-gated acct `7025****1161`, 15 calls, ~$0.00018, negatives battery — `evidence/aws-validation.json` |

## F. Portal & UX

- **Screens/flows**: `/api-operations` — 3 tabs + deep links: services
  inventory, service detail (indicators/absent_indicators/PF verdict/
  operations provenance/metrics/SLO/sources), incidents + detail
  (ladder OBSERVED→VERIFIED/REFUTED, merged timeline), investigation
  POST (scope-gated, blocked-state on 403), status (health,
  capability stage, integrations, source freshness).
- **Real data**: every render backed by live `/v1` — zero mocks in the
  chain (`portal-verification.json` `generated_by`).
- **Browser tests**: Chromium 19/19 + 7 screenshots; DOM/console token
  scan clean.
- **A11y**: 11 checks (single h1, scope=col, roles, aria-live,
  keyboard); i18n 1 691/1 691 key parity en+pt-BR.
- **Limitations**: same-origin proxy topology required (CSP); sources
  panel lacks per-source error/retry fields (F021); `context_pack`
  summarized not expanded.

## G. Federated E2E

`lab/federated_e2e.py` — 18-step golden path over real processes (demo
API + real dependency on :8320/:8330), real installs (clean wheel →
fresh venv), real peers; every step writes an oracle record with truth
label. Receipt: `evidence/federated-e2e-receipt.json` (17 records at
snapshot; S18 wrote after).

Truth table: **16 pass / 2 BLOCKED / 0 fail** — S1–S8 LOCAL_REAL
(bootstrap → register → 783-req traffic → ingest → SLO MET → 100%-503
degradation → incident), S9–S11 + S14 INTEGRATION_REAL (Doctor,
Atlas, the-forge routed ask, Portal browser), **S12/S13 BLOCKED**
(Bridge/Lens named unavailable + operator instructions), S15–S18
LOCAL_REAL (recovery SLO MET with 0 new anomalies, backup/restore
parity, 40/40 security battery, manifest).

## H. Security

- **Auth**: `X-Sentinel-Role` self-declaration dead — bearer
  (`secrets.token_urlsafe(32)`, `compare_digest`, env>stdin>file, TTL
  option) + HMAC integrated principal (±300 s skew, single-use nonce,
  workspace match). `401`+problem+json taxonomy; loopback default;
  `--allow-remote` opt-in warning.
- **Boundary**: correlation ids end-to-end; `transport_state` ≠
  `execution_state`; `resolve_under_root` path guards; 32 MiB bounded
  reads; ingest correlation checks; `handoff_version` required (F022).
- **Battery**: `lab/security_battery.py` — **40/40 held**
  (API 15, storage 5, agentic 5, cloud 13, UI 2) —
  `evidence/security-results.json`.
- **Residual**: no P0; F007 bounded to loopback/single-user scope;
  tokens never persisted outside workspace/`0600` best-effort; no
  credential material in any receipt (SEC-CLD-13, SEC-UI-01).

## I. Performance

F006 reproduced then fixed (`12-performance-report.md`,
`performance-{before,after}.json`):

- **Before**: 10 000 dedup + 10 000 service point queries + up to
  2 880 overlap queries per 10k-obs run.
- **After**: all three collapsed to constant batches (≤26 / 2 / ≤2);
  elapsed −24 % dispersed, −10 % large_file, noise-level elsewhere.
- **Parity**: identical dataset sha256 + identical
  accept/skip/quarantine/store counts across all 10 shapes; 5
  regression tests; tracemalloc peaks + DB size recorded.
- Scope honesty: the win is query-complexity, not a universal latency
  promise (insert+validation dominate wall-clock).

## J. Release Readiness

- **CI**: `ci.yml` — windows+ubuntu / py3.12 / ruff+mypy+pytest+build+
  install-smoke. Cycle-10 batteries run inside `pytest -q` (contract,
  security, adapter conformance, offline socket guard). Hosted run at
  HEAD not yet observed → condition #1.
- **Packaging**: `uv build` clean; wheel ships 6/6 migrations + typed
  contracts; sdist ships tests + 8 JSON schemas; fresh-venv smoke PASS
  (`evidence/installation-results.json`).
- **Install**: `install -y` non-interactive proven (incl. path with
  spaces, repeat idempotent); interactive wizard / ps1/sh scripts /
  unicode / native-Linux honestly not-run (`14-packaging-installation.md`).
- **Compatibility**: `docs/16-compatibility-matrix.md` + cycle-10
  `16-compatibility-matrix.md` — real verified versions.
- **Recovery**: storage battery (12 tests) + e2e S16 parity.
- **Decision**: **CONDITIONAL GO** — `18-release-readiness.md`.

## K. Next Cycle Recommendation

Propose **Cycle 11 only around observed gaps** — no new feature axes:

1. **Close the named-blocked seams where peers now allow it** — if
   forge-agent-bridge grows a session intake (or provider transport +
   credentials land), homologate S12; same for Lens refs (S13). Pure
   homologation work, gated on peer availability — *not* a reason to
   build a second transport inside the Sentinel.
2. **Hosted CI observability** — first real GHA run at HEAD; if green,
   the CONDITIONAL GO's condition #1 clears (no code work, just
   observation + record).
3. **F021 additive surface** — project per-source `last_error`/retry/
   quarantine-depth into `/v1/services/{id}` so the Portal sources
   panel stops flagging not-exposed. Small, additive, contract-visible.
4. **F020 one-place fix** — pin the 8317 default in the remaining spec
   text (trivial docs).
5. **the-forge probe tier** (peer-side, recommended) — fixture-root
   execute probes so `provider check` can pass storage-backed
   providers; recorded as C10-F016's recommendation.
6. **Do NOT start**: marketplace, remote transports, distributed
   runtime, or any production-validated marketing — nothing in the
   cycle-10 evidence justifies them.
