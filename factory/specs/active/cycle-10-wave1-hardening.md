---
id: cycle-10-wave1-hardening
title: "Cycle 10 Wave 1 — Sentinel security, contract & runtime hardening"
agent: claude
risk: high
verification:
  - "python -m pytest -q"
  - "python -m ruff check ."
  - "python -m mypy src"
---

# Context

Implements prompt_evo_cycle10.md §5 (Phase 1) on the `cycle-10` branch. INPUTS: `docs/cycles/cycle-10/02-revalidated-findings.md` (fix the P0/P1s attributed to this repo first), `docs/07-security-threat-model.md`, `src/api_sentinel/api/` (current X-Sentinel-Role placeholder — C10-F007 is the trust failure to fix).

# Acceptance Criteria

- [x] Real auth on the local API replacing self-declared `X-Sentinel-Role`: local mode = generated token file in workspace (0600-ish perms, `secrets.token_urlsafe`, `hmac.compare_digest` compare, token required whenever server reachable beyond same-process TestClient OR configurable); integrated mode = trusted principal propagated only when a shared-secret/signature header validates — self-declared role headers from untrusted clients REJECTED. Role→scope map enforced server-side (viewer/analyst/operator/admin per docs/07). Backward-compat: existing tests using TestClient keep working via a documented test-token fixture. — `infrastructure/security/auth.py`, `api/deps.py::_authenticate`; TestClient seam = `ApiContext(auth=None)`, unreachable via serve.
- [x] Negative auth battery passing: no token, bad token, expired (if TTL), role spoofing, cross-workspace, missing resource, self-declared admin, replay, CORS not wildcard-credentialed, forwarded headers from untrusted proxies ignored, denial happens BEFORE lookup (no existence leak). — `tests/security/test_api_auth.py` (28 cases).
- [x] Handoff security hardening: canonical paths under workspace `handoffs/` + `receipts/` roots (canonicalize, reject `..`/symlink escape), bounded input/output sizes, timeouts on any subprocess path, no `shell=True`, correlation id on every handoff+receipt, separate transport-vs-execution receipt fields. — `infrastructure/security/paths.py` guards + `read_text_bounded` (32 MiB) across adapters; `correlation_id` on handoff+receipt; `transport_state`≠`execution_state`. No subprocess paths exist in `src/` (grep-verified) — timeout/shell elements trivially met.
- [x] Capabilities semantics per §5.4: states documented (IMPLEMENTED/TESTED/INTEGRATED/AVAILABLE/UNAVAILABLE/UNVERIFIED/BLOCKED or reuse existing enum honestly); `/v1/capabilities` reports stage, not just presence — installed optional package alone never flips an integration to operational. — `CapabilityStage` enum (implemented/tested/integrated/verified/production_validated) + `CapabilityStatus`; `/v1/capabilities` emits stage+status+since+notes per the Wave-0 matrix.
- [x] Contract conformance negative suite per §5.3: unknown version, removed field, missing required, invalid unit/dimension/timestamp, dup id, inconsistent correlation id, hash mismatch, truncated payload, broken ref — each produces a typed refusal, never silent acceptance (extend `tests/contract/test_schema_compat.py`). — `TestCycleTenNegatives`; `sha256:<64hex>` enforced in model+schema; Atlas dup ids rejected.
- [x] Storage recovery revalidation per §5.5: wheel contains migrations (regression test against built wheel in temp venv), interrupted backup, corrupted backup, restore without --force refused, post-restore integrity, retention with active incident protected, orphan artifacts, append-only journal, crash-during-ingest recovery — tests present and green. — `tests/security/test_storage_recovery.py`; wheel test builds real artifact via `uv build` + zip inspection (skip=uv absent, named state).
- [x] `docs/cycles/cycle-10/05-security-hardening.md` + `evidence/security-results.json` with real outputs. — includes live smoke transcript (401/401-spoof/200) and verification tails.
- [x] All Wave-0 P0s fixed; P1s fixed or explicitly deferred with rationale in the findings ledger (status field updated). — no P0s existed; F007+F012 FIXED, F009/F010/F011→Wave 2, F005→Wave 3, F006→Wave 4; new C10-F014 (P3) recorded.

# Constraints

- No new required deps; token file perms best-effort on Windows (document).
- Keep `--json` CLI contract + all Cycles 0–9 behavior.
- Auth must not break `serve` default loopback dev ergonomics — generated token printed once at install/first-serve, stored in workspace config (never committed).
- 100-col lines; domain stays pure (auth lives in api/deps + infrastructure/security).

# Review Notes

- Verify denial-before-lookup on every protected route (no existence oracle).
- Verify token is never logged/receipted.
- Verify TestClient path documented for tests.

# Grill Gate

- Owner: Edgar
- Out of scope: corporate SSO/IdP, mTLS, non-loopback production deployment.
- Riskiest assumption: workspace-local token is acceptable "local mode" security for a loopback tool.
- Smallest acceptable version: token auth + negative battery + handoff hardening + capabilities stage.
- _Depends: cycle-10-wave0-baseline_
