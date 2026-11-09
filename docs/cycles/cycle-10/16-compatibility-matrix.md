---
cycle: 10
deliverable: compatibility matrix — platform, packaging, peers, contracts (final verified state)
wave: 5
---

# Cycle 10 — Compatibility matrix (final)

Canonical product-level matrix refreshed in `docs/16-compatibility-matrix.md`;
this file is the cycle-10 evidence view. Every row cites a receipt.

## 1. Platform / runtime

| Axis | Verified | Evidence |
|---|---|---|
| Python | 3.11 floor (`requires-python`, mypy `python_version=3.11`) · **3.12.13** exercised (dev venv, fresh-venv smoke, CI matrix) | `pyproject.toml`, `evidence/installation-results.json` |
| OS | **Windows 11** (all waves ran here) · **Linux** (CI `ubuntu-latest` — push-triggered, not yet observed at HEAD) | `ci.yml`, wave receipts |
| Package build | `uv build` clean — wheel 127 files, sdist 190 files | `14-packaging-installation.md` |
| Wheel contents | 6/6 SQL migrations + typed contract models inside; no PYTHONPATH need | zip inspection, `evidence/installation-results.json` |
| sdist contents | src + tests (42) + `contracts/v1` (8 schemas) + README/CHANGELOG/LICENSE | tar inspection |
| Fresh-venv install | wheel `[api]` → `install`/`doctor`/`capabilities`/`mcp` probes all PASS; path-with-spaces + repeat-install | `evidence/installation-results.json` |
| Optional extras | `api` (serve), `aws` (boto3), `mcp` (stdio server), `dev` (gates) | `pyproject.toml`; missing extra → typed named error, probed |
| Console encodings | UTF-8 reconfigure for cp1252 consoles (`1c616a5`) | unit tests; full cp1252 serve session not exercised (named) |
| Git presence | workspace init works with and without `.git` — project scope is a plain directory | `install` in `%TEMP%` dirs (no repo) |

## 2. Peer seams (final states)

| Peer | Version exercised | Final state | Evidence |
|---|---|---|---|
| forge-doctor-api | **0.3.1** (editable install in sentinel venv) | **INTEGRATED** — in-process `DoctorBoundary` completed, 10 findings/9 unknowns; protocol v2 emission, v1/v2 ingest | `evidence/doctor_inprocess_run.json`, `…_receipt.json` |
| code-atlas-forge | **1.1.1** (real `atlas.db` snapshot 11) | **INTEGRATED** — `DbStructuralContext` read-only seam; `AtlasExport/v1` file seam still supported | `evidence/atlas/`, `evidence/atlas_correlate_run.json` |
| the-forge | `cycle-10-apisentinel` @ `5aea039` (adapter) | **INTEGRATED** — registry `state=ready`, 8/8 pairs execute, receipts persisted | `evidence/theforge/runs/` |
| forge-portal | `cycle-10-sentinel` @ `52a21e6` | **INTEGRATED** — dedicated `SentinelApi`, same-origin proxy, Chromium 19/19 | `evidence/portal-verification.json` |
| forge-agent-bridge | 0.2.1 audited, no session transport | **NAMED `unverified\|file`** | `09-agent-bridge-and-lens.md`, e2e S12 |
| forge-lens | 0.2.1 audited | **NAMED `unavailable\|file`** | same, e2e S13 |
| api-forge | 0.3.2 audited | **NAMED `unverified\|handoff_file`** | same |
| data-sentinel-forge | 0.5.2 audited | **NAMED `unverified\|handoff_file`** | same |
| product-forge | — | deliberately independent — `/api-operations` renders with PF absent | `evidence/portal-verification.json` |
| AWS (real) | acct `7025****1161` (`sha256:51e88bde…`), `us-east-1` | **EXTERNAL_REAL bounded** — STS gate, 15 calls, read-only proof + negatives | `evidence/aws-validation.json` |

## 3. Contract surface (v1, unchanged wire shapes)

| Contract | Status at HEAD |
|---|---|
| `api-sentinel/{ServiceIdentity,RuntimeObservation,Anomaly,InvestigationHandoff,AtlasExport,TelemetrySource,DeployManifest,EvidenceArtifact}/v1` | unchanged wire; `InvestigationHandoff` model requiredness aligned to schema (F014 — reader-side tightening only) |
| `theforge/ExecutionResult/v1` | conformant — all 8 execute pairs validated by real transport (`verification.json` status=passed) |
| `forge/SpecialistAgenticManifest/v1` | `forge.agentic.json` — `cli_entry`+`mcp_command` both resolve (F002 refuted, F012 fixed) |
| `application/problem+json` /v1 errors | unchanged; auth failures now `401`+problem+json with `details.reason` (F007) |

**Silent contract changes: none.** The only contract-visible decisions
(F014 requiredness, F015 manifest declaration rule, F018 evidence
re-emission) are recorded in `02-revalidated-findings.md` +
`04-contract-compatibility.md` — never silent.

## 4. Auth surface

| Mode | Verified |
|---|---|
| `Authorization: Bearer <token>` | live smoke 200/401 (Wave 1) + portal e2e |
| `X-Sentinel-Principal` + HMAC signature | unit-covered (skew, nonce replay, workspace match) |
| no credential / self-declared role | `401 missing_credentials` — spoof dead |
| `--allow-remote` | opt-in, loud warning; loopback remains default |
