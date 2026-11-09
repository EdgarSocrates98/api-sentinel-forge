---
cycle: 10
deliverable: portal api-operations surface — real Sentinel data end to end
wave: 3
---

# Cycle 10 Wave 3 — Portal `/api-operations` on the real Sentinel surface

Owns **C10-F005** (Portal rendered the operations journey from Product
Forge `forge/PortalApi/1` data, structurally incompatible with the
`api-sentinel/v1` contract). Per spec §7 the fix is a Sentinel-specific
adapter — `PfApi` stays untouched, the two provenances never merge.

Evidence: `evidence/portal-verification.json` in this directory (real browser run, real
seeded workspace, real `serve` process — no mocks anywhere in the
chain). Portal repo: `forge-portal`, branch `cycle-10-sentinel`,
base `1e85c28`.

## 1. C10-F005 — dedicated `SentinelApi`, never a bent envelope

**Before (verified at wave 0):** `/api-operations` injected `PfApi`
and rendered Product Forge product/spec/health entities as if they
were Sentinel runtime state. Sentinel returns typed model payloads
(plus the honest-read fields `unknowns` + `evidence_refs`) and
`application/problem+json` errors — there is no `forge/PortalApi/1`
envelope to unwrap. The surface could therefore never show real
telemetry; it rendered an unrelated inventory.

**After:** `src/app/sentinel/sentinel-api.service.ts` — a separate
injectable client that:

- builds every request as `{baseUrl}/v1/*` (paths enumerated, never
  hand-concatenated outside the service; ids `encodeURIComponent`ed);
- sends `Authorization: Bearer <token>` only when configured — the
  token resolves from the `sentinel.token` session key (connection
  card in the UI), is never logged, never placed in a URL, and is
  actively scrubbed from `localStorage`;
- keeps `unknowns`, `evidence_refs`, `limitations`, `absent_indicators`,
  per-source `excluded_by_reason` and quality vocab verbatim — the
  adapter unwraps nothing, invents nothing;
- maps `problem+json` (`type/title/status/detail/code/details`) onto
  the portal `ApiError` carrying the verbatim `sentinel.<code>` so
  `ui-error` renders the real refusal (`unauthorized`, `forbidden`,
  `not_found`, `conflict`, `integration_refused`), and transport
  failure becomes a retryable `PORTAL-UNREACHABLE`-class error.

`src/app/sentinel/sentinel.models.ts` mirrors the Pydantic schemas
(`api/schemas.py`, `application/use_cases.py`, `domain/models.py`,
`incidents/models.py`, `reliability/*`) one-to-one: health, capabilities
with `stage`, service summary/detail/links, operations, metrics windows,
SLO definitions + assessments, incidents + journal + hypotheses +
verifications + change events + dependency observations, investigations,
anomalies, evidence metadata, integrations.

`src/app/sentinel/sentinel-states.ts` is the honest-state map:
`AVAILABLE/UNAVAILABLE/PARTIAL/UNVERIFIED/UNKNOWN`, the health
conditions (`observed…no_observations`), `MET/VIOLATED/
INSUFFICIENT_DATA/INCOMPATIBLE_INPUTS`, the confidence ladder
`OBSERVED→CORRELATED→HYPOTHESIZED→VERIFIED` (+REFUTED terminal),
workflow statuses, severities, capability stages — each to a badge
tone via the new `kindOverride` input on `ui-state-badge` (additive;
the built-in vocabulary is unchanged). `fmtRatio/fmtMs/fmtRate`
render `—` for null — an absent indicator is never a zero.

`PfApi` and `forge/PortalApi/1` are not referenced anywhere in the
page subtree.

## 2. The wired journey (spec §7.4)

`/api-operations` (`src/app/pages/api-operations/`) — standalone
components, signals, `?tab=&service=&incident=` query params via
component-input binding so every state deep-links:

| Tab | Panels | Endpoint(s) |
| --- | --- | --- |
| Services | KPI strip (services/AVAILABLE/open incidents/SLO defs), inventory table (health badge, condition, last window, err ratio, p95, open incidents, SLO count), unknowns line | `GET /v1/services` |
| Service detail | identity + env/namespace, state+condition badges, evaluated-at + latest window, indicator grid w/ explicit units (`req/s`, `ms`, `%`), `absent_indicators`, `limitations`, PF correspondence verdict (`proven/declared/unproven` + reason + external refs, never name-inferred), operations table (declared/observed provenance), metrics window table (last 12, quality chips, gaps), SLO cards (state chip or `NOT_RUN`, SLI, budget remaining, coverage, limitations), telemetry sources w/ freshness | `/services/{id}` `/operations` `/metrics` `/slos` |
| Incidents | open/all filter (server-side `?status=open`), table (severity, status, service, detected) | `GET /v1/incidents` |
| Incident detail | severity+status badges, first-observed/detected/resolved, rule refs (`detector@version rule@rev`), hypothesis ladder UI (rung progression + REFUTED), evidence_for/against counts, verifications, anomalies (explanation + math + rule), evidence metadata + unresolved refs, merged timeline (journal + change events + dependency observations + hypotheses + verdicts, truncated flag honored), limitations | `/incidents/{id}` `/incidents/{id}/timeline` |
| Investigations | `POST /v1/incidents/{id}/investigations` — scope-gated button; renders status, `replayed` flag, verdict, suspected origin, unknowns, structural context excerpt; `403 sentinel.forbidden` renders as a blocked state, not an error page | as left + `GET /v1/investigations/{id}` |
| Sentinel status | `/v1/health` (state, version, workspace, db, capability counts, full self-observability block), `/v1/capabilities` (status + **stage** + since + notes), `/v1/integrations/status` verbatim rows, per-service source freshness from `/v1/services` | health/capabilities/integrations/services |

Every async panel independently renders loading / data / empty /
per-surface error (`Promise.all` with per-call error capture — one
unavailable panel never blanks the page). No fabricated zeros,
no arbitrary health percentages, explicit units, sampling/quality
indicators (`COMPLETE/PARTIAL/SAMPLED/STALE/INVALID`).

## 3. Deployment architecture discovered: `connect-src 'self'`

The canonical portal server (`portal_core/forge_portal.py`, vendored —
not editable) serves the SPA with `Content-Security-Policy:
connect-src 'self'`. A cross-origin `api-sentinel` **cannot be fetched
from the served page at all** — the browser blocks before the network
(verified: zero requests reach uvicorn). `api.cors_origins` (the
Sentinel-side opt-in, verified working via curl) only matters if the
page itself is served without the strict CSP.

The working, documented production pattern — proven by the real
browser run — is a **same-origin `/sentinel` reverse proxy** in front
of the bundle: `sentinel.baseUrl = <origin>/sentinel`; the proxy
forwards `GET/POST` + `Authorization`, strips cookies/Origin/Referer
(hop-by-hop + `forge_session` never reach Sentinel), and Sentinel
needs no CORS config. The e2e ships exactly this handler
(`e2e/test_sentinel_e2e.py::make_handler`) so the verification matches
the recommended deployment 1:1.

Recorded as **C10-F019** in `02-revalidated-findings.md`.

## 4. Real verification (`e2e/test_sentinel_e2e.py`)

Seed (real installed CLI, subprocess only — the lab recipe from
`lab/e2e_validate.py`):

```
api-sentinel install --yes --workspace WS --json
services --register payments-api | checkout-api
sources --register jsonl-file --service-id <id>
ingest --source-id <id> --file lab-001.jsonl            # 8 obs
ingest --source-id <id> --file lab-004/observations.jsonl  # 18 obs
changes import --file lab-004/deploy-manifest.json      # 2 changes
slo define --service-id payments --target 0.99 --window 1h
slo evaluate --slo-id <id> T0..01:00                    # MET
detect --service-id payments T0..00:08 --now 00:12      # latency anom
detect --service-id checkout 00:04..00:10 --now 00:12   # 503s anom → incident
changes correlate --incident-id <inc>                   # deploy ranked
hypotheses add --confidence CORRELATED --evidence-for <chg>
hypotheses verify + promote                             # VERIFIED
serve --host 127.0.0.1 --port 8317 --workspace WS       # env token
```

Browser (Chromium, Playwright — same harness as the repo's browser
e2e): connect via the real UI card → inventory → service detail →
incident list → incident detail (ladder + timeline) → **Run
investigation** (real POST, `Completed`) → status (capabilities +
stage). All 19 recorded steps pass; 7 screenshots + results JSON in
`forge-portal/e2e/artifacts/sentinel-*`; the token never reaches the
DOM or console (asserted). Product Forge is deliberately absent in
the harness — the surface renders fully anyway (§7.3 independence).

Sentinel-side code changes: **none** — every shape the UI needed was
already served. AC's "additive endpoints if genuinely needed" needed
nothing.

## 5. Follow-on findings appended to the ledger

- **C10-F019 (P2, portal deployment)** — `connect-src 'self'` on the
  canonical portal server makes same-origin proxying the only
  workable topology for Sentinel-backed pages; documented in §3
  above + the wave-3 e2e harness.
- **C10-F020 (P3, contract doc)** — the spec and the CLI disagree on
  the default serve port (`8080` in docs vs the `8317` the portal
  now defaults to); Portal honors `8317` per user direction, but the
  mismatch should be decided once, in one place.
- **C10-F021 (P3, surface gap)** — `/v1/services/{id}` does not emit
  per-source `last_error`/retry/quarantine-depth fields the §7.4
  "Sources view" lists; the view shows registered/observations/
  window/stale honestly and flags the rest as not-exposed instead of
  inventing them.
