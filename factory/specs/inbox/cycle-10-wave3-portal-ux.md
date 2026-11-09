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

- [ ] Portal fetches REAL Sentinel data: a `SentinelApiClient` (or extend existing service pattern) hitting `http://127.0.0.1:8080/v1/*` with the auth token mechanism from Wave 1; config via environment/portal settings — never hardcoded secrets.
- [ ] `/api-operations` journey per §7.4: Overview (observed services, source states, active incidents, SLOs, coverage/incomplete-data indicators) → Service Detail (identity, operations, rate/error/latency, SLOs, anomalies, incidents, related deploys, dependencies w/ origin class) → Incident Detail (title/severity/status/timeline/evidence/detectors/hypotheses/changes/specialists/limitations/next verifications) → Investigation Detail (context pack, Doctor results, Atlas context, handoffs, bridge session state, lens refs, receipts, per-step status) → Sources view (configured/state/last-obs/freshness/coverage/errors/quarantine/retries/budgets/permissions).
- [ ] Product Forge vs Sentinel separation per §7.3: product context shown as complementary with explicit provenance; no identity merge without proven correspondence; Sentinel works when PF data absent (independent state).
- [ ] Honest states per Portal's existing vocabulary (map Sentinel AVAILABLE/UNAVAILABLE/PARTIAL/UNVERIFIED/UNKNOWN + Portal's own); no arbitrary health %; explicit units; sampling indicators; empty states explain why; no dead buttons (every action wired or removed).
- [ ] Security visuals per §7.7: no XSS (sanity on log-derived strings), safe external links, evidence access authorized, no token in DOM/storage beyond session needs, downloads bounded.
- [ ] Accessibility per §7.8: keyboard nav, focus mgmt, ARIA labels, contrast, PT-BR/EN i18n keys added for all new strings; axe-friendly markup.
- [ ] Tests: Angular unit tests for new components/services (real patterns from existing specs); e2e browser flow (existing e2e/ harness — Playwright or whatever the repo uses) proving §7.9: open API Operations → select real service → see persisted telemetry → open incident → timeline → evidence → run investigation → see result + specialists + receipt — WITHOUT the CLI.
- [ ] Sentinel side (small): any endpoint gaps the UI truly needs (e.g. `GET /v1/services/{id}/links`, investigation detail shape, sources freshness fields) — additive only, version-safe.
- [ ] `docs/cycles/cycle-10/10-portal-api-operations.md` + `evidence/portal-e2e-results.json` (real browser run output).

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
