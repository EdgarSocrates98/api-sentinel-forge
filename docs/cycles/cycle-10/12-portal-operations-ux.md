---
cycle: 10
deliverable: portal operations UX — accessibility, honesty, verification
wave: 3
---

# Cycle 10 Wave 3 — Portal operations UX verification record

Companion to `10-portal-api-operations.md` (contract + journey). This
file records the UX/a11y/security/i18n execution per spec §7.7–§7.9 and
the evidence pointers. Machine-readable results:
`evidence/portal-verification.json` (this directory).

## UX surface decisions

- **One page, three tabs** (`?tab=services|incidents|status`,
  `&service=`/`&incident=` deep links via component-input binding) —
  the page was previously PfApi-backed; the query-param model keeps
  browser history + shareable links working through the new adapter.
- **Honest-state vocabulary everywhere**: `ui-state-badge` gained an
  additive `kindOverride` input so Sentinel states map to tones without
  touching the built-in vocabulary; every state label resolves through
  `sentinel.state.*` i18n keys (unknown future states degrade to the
  raw contract token — never a guessed label).
- **Null vs zero**: `fmtRatio/fmtMs/fmtRate` emit `—` for absent
  indicators; SLO entries without an assessment render a `NOT_RUN`
  chip; `absent_indicators`, `limitations`, `unknowns` render as their
  own warning lines on every panel that carries them.
- **Per-surface failure isolation**: detail bundles fetch each
  sub-resource with its own error capture — a `404`/scope denial on
  `/slos` does not blank the health hero.
- **Investigation mutation**: explicit button, disabled while running,
  `idempotency_key` supported; `403 sentinel.forbidden` renders as a
  named **blocked** state (`sentinel.inv.blocked`) instead of a crash.

## Accessibility (static gates + runtime)

- `a11y.spec.ts` — all 11 checks green on the new templates:
  exactly one `ui-page-header` (the h1) on the routed page; no stray
  `<h1>`; every `<th>` has `scope="col"`; clickable rows are
  `tabindex="0"` + `(keydown.enter)`; `role="group"`/`role="region"`/
  `role="list"` carry `aria-label`; `aria-live="polite"` on the
  connection result + investigation areas; `role="status"`/`role="alert"`
  on async feedback.
- `ui-state-badge` already renders `role="status"`; timeline is a real
  `<ol>`; the hypothesis ladder is `role="list"`/`role="listitem"` with
  an accessible name.
- Keyboard: tabs are router links (native focus/Enter); detail rows
  Enter-activate; the connection card is a real `<form>` with labelled
  inputs (`<label>` wraps, `type="password"` for the token).

## i18n

- 180 new keys under `sentinel.*` in both `en` and `pt-BR`
  (`check_i18n.py`: 1691/1691 parity, 0 hardcoded prose on the new
  templates). State labels localize (`Disponível`/`Available`,
  `Violado`/`Violated`, `Dados insuficientes`/`Insufficient data`…);
  contract tokens (service ids, rule refs, ULIDs, kinds) stay verbatim.

## Security

- Bearer via `Authorization` header only — never in URLs (asserted in
  unit specs + the e2e's DOM/console token scan: `token_in_artifacts:
  false`).
- Token persists to `sessionStorage` only; `localStorage` key actively
  scrubbed on write (same policy as `pf.token`).
- `problem+json` `detail` strings render through interpolation
  (Angular sanitization) — no `[innerHTML]`/bypass paths added;
  `ui-icon`'s compile-time constant set is the only `bypassSecurityTrustHtml`
  caller and is unchanged.
- Evidence stays reference-oriented (`GET /v1/evidence/{id}` metadata
  only; no blob download surface — nothing to bound).
- `kindOverride` maps strictly to badge tones; no state string reaches
  a sink.
- The e2e proxy strips `Cookie`/`Origin`/`Referer` before forwarding —
  the portal session token can never leak into Sentinel requests
  (documented deployment pattern, §3 of the companion doc).

## Known UX limits (honest, recorded)

- Sources view: `last_error`, retry budget, per-source quarantine depth
  are not exposed by the API — the panel renders
  registered/observations/latest window/stale and nothing more
  (C10-F021).
- Investigation detail: renders verdict/suspected origin/unknowns/
  structural excerpt + status; per-step specialist receipts exist in
  `context_pack` but are summarized, not expanded (bounded detail kept
  deliberate).
- `X-Sentinel-*` integrated auth mode is out of scope — browser path is
  bearer-token only (viewer-level reads + `investigate` scope via the
  workspace token).
