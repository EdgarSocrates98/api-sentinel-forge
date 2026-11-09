---
id: cycle-10-wave3-portal-ux
title: "Cycle 10 Wave 3 — Forge Portal API Operations powered by real Sentinel backend"
agent: claude
risk: high
verification:
  - "cd ../forge-portal && npm test -- --watch=false 2>&1 | tail -20"
  - "python -m pytest -q"
---

# Context

Implements prompt_evo_cycle10.md §7 (Phase 3). Sentinel work on `cycle-10`; Portal work in `E:\projetos\FORJAS\forge-portal` on branch `cycle-10-api-operations` (dedicated branch, separate commits, PR later). INPUTS: `docs/11-portal-ux-design.md`, Sentinel `/v1/*` surface (Cycle 6), forge-portal Angular app (real codebase — inspect first: `/api-operations` route, `ApiOperationsPage`, PfApi models, PortalApi service, design system, i18n, tests, e2e/).

# Acceptance Criteria

- [x] Portal fetches REAL Sentinel data: `SentinelApi` (`src/app/sentinel/sentinel-api.service.ts`) hitting `http://127.0.0.1:8317/v1/*` (default; `sentinel.baseUrl` runtime-configurable — spec's `8080` recorded as C10-F020) with `Authorization: Bearer` from session storage — never hardcoded secrets.
- [x] `/api-operations` journey per §7.4: Overview (services KPI strip, inventory w/ health+condition, open incidents, SLO defs, unknowns) → Service Detail (identity, indicators w/ units, PF correspondence verdict, operations provenance, metrics windows, SLO cards, telemetry sources) → Incident Detail (severity/status, first-observed/detected, rule refs, hypothesis ladder, evidence counts+refs, verifications, anomalies, merged timeline, limitations) → Investigation (verdict, suspected origin, unknowns, structural context excerpt, replayed flag) → Sources view (registered/state/last-obs/freshness/stale — errors/quarantine/retries/budgets not exposed by API, recorded as C10-F021).
- [x] Product Forge vs Sentinel separation per §7.3: `PfApi` untouched, zero references in the page subtree; PF correspondence shown as provenance-verdict only (proven/declared/unproven + reason, never name-inferred); Sentinel tab fully rendered with PF absent in the e2e harness.
- [x] Honest states per Portal's existing vocabulary (`kindOverride` on `ui-state-badge` maps Sentinel AVAILABLE/UNAVAILABLE/PARTIAL/UNVERIFIED/UNKNOWN + health conditions + SLO states + confidence ladder); no arbitrary health %; explicit units (`req/s`, `ms`, `%`); quality chips; `—` for null indicators; `NOT_RUN` chips for unevaluated SLOs; empty states name the missing input; every action wired (investigation button scope-gated, 403 → blocked state).
- [x] Security visuals per §7.7: no `[innerHTML]`/bypass paths added; `problem+json` detail through Angular sanitization; evidence as metadata refs only (no blob downloads); token in `sessionStorage` only, scrubbed from `localStorage`, absent from DOM/console/artifacts (e2e-asserted); proxy strips Cookie/Origin/Referer.
- [x] Accessibility per §7.8: `a11y.spec.ts` 11/11 green on new templates (single h1 via `ui-page-header`, `scope="col"`, `tabindex`+Enter on rows, labelled `role="group/list/region"`, `aria-live` on async feedback); keyboard: router-link tabs, Enter-activating rows, real `<form>` connection card; PT-BR/EN parity 1691/1691, 0 hardcoded prose.
- [x] Tests: 23 new specs — `sentinel-api.service.spec.ts` (11: request paths, bearer, `problem+json` → `sentinel.<code>` ApiError, unreachable → PORTAL-UNREACHABLE) + `sentinel-states.spec.ts` (8: state mapping/ladder ranking) + rewritten `api-operations.page.spec.ts` (4, fake `SentinelApi`); suite total 624/57 files; e2e `e2e/test_sentinel_e2e.py` (Chromium, real CLI-seeded workspace + real `serve`) proving §7.9: connect → inventory → service detail → incident → timeline → evidence → **real investigation POST** → status — 19/19 steps, `verdict: pass`, WITHOUT the CLI in the browser path.
- [x] Sentinel side (small): **no additive endpoints needed** — every consumed shape was already served; two surface gaps recorded honestly as findings (C10-F021) instead of faked.
- [x] `docs/cycles/cycle-10/10-portal-api-operations.md` + `docs/cycles/cycle-10/12-portal-operations-ux.md` + `docs/cycles/cycle-10/evidence/portal-verification.json` (real browser run output, verbatim steps from `forge-portal/e2e/artifacts/sentinel-e2e-results.json` + 7 screenshots).

# Constraints

- Reuse Portal design system components; no new UI library without justification.
- No fake data: every rendered value traces to a Sentinel response or an honest state.
- Don't transform Product Forge into a Sentinel dependency.
- Sentinel must remain fully functional without the Portal.
- Portal changes on `cycle-10-api-operations` branch in ITS repo — never main.

# Review Notes

- Verify a real Sentinel server backed the e2e run (evidence: server log/pid + seeded workspace).
- Verify no buttons without implementation; error states clear.
- Verify i18n coverage both locales.

# Grill Gate

- Owner: Edgar
- Out of scope: Portal-wide redesign, PF feature work, mobile-native.
- Riskiest assumption: Portal backend contract (`forge/PortalApi/1`) tolerates a second data source cleanly.
- Smallest acceptable version: Overview + Service + Incident details real-wired + sources view + browser e2e on the golden path.
- _Depends: cycle-10-wave1-hardening (auth token), cycle-10-wave2-federation (investigation details shape)_
