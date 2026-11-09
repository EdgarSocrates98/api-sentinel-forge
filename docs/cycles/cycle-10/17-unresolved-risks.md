---
cycle: 10
deliverable: unresolved risks — open findings, blocked items, residual exposure
wave: 5
---

# Cycle 10 — Unresolved risks (final register)

Everything open at release decision time. Nothing here is hidden —
each item maps to the findings ledger (`02-revalidated-findings.md`)
and the release gates (`18-release-readiness.md`).

## 1. Blocked / waiting on peers or environment

| Item | Finding | State | What unblocks it |
|---|---|---|---|
| Agent Bridge session transport | C10-F004, e2e S12 | **BLOCKED** — no `forge_agent_bridge` on host; no session-import seam exists bridge-side. Governed envelopes emit `delegated`, never `executed` | Bridge-side intake command or provider transport + credentials |
| Forge Lens trace correlation | e2e S13 | **BLOCKED** — no `forge_lens` module, no `lens.refs` manifest configured | Lens refs manifest export or module install |
| API Forge consumption | C10-F008 family | `unverified\|handoff_file` — emit-only | apiforge file-ingest entrypoint or in-process install |
| Data Sentinel consumption | — | `unverified\|handoff_file` (optional, domain-triggered) | data-sentinel install + stale-data scenario |
| Hosted CI (GHA) at HEAD | §14 | **NOT OBSERVED** — workflow exists (`ci.yml`, win+linux, ruff/mypy/pytest/build/install-smoke); first real run happens on push/PR | push `cycle-10` or open the PR |
| `theforge provider check` execute probe | C10-F016 | **NAMED** — conformance harness probes an empty scratch workspace; a storage-backed provider honestly refuses `APISENT-WORKSPACE-UNAVAILABLE`. Protocol gates all green | the-forge-side probe tier accepting a fixture root (peer recommendation recorded) |
| `forge/v1` param channel | C10-F017 | **WORKED AROUND** — wire carries no task params; scope resolves explicit → single-service → named ambiguity | the-forge TaskSpec constraint channel (peer-side) |
| AWS `login_session` profiles | C10-F024 | **DOCUMENTED** — botocore can't resolve `aws login` profiles without `botocore[crt]`; env-exported session creds used | `pip install botocore[crt]` or keep the `aws configure export-credentials` path |

## 2. Open product gaps (recorded, non-blocking)

| Item | Finding | Severity | State |
|---|---|---|---|
| Serve-port doc drift (8080 vs 8317) | C10-F020 | P3 | OPEN — docs/portal default to 8317; recommend fixing the reference in one place (README + `10-portal-api-operations.md` already use 8317; residual: §7 spec text) |
| Per-source error/retry/quarantine fields not in `/v1/services/{id}` | C10-F021 | P3 | OPEN — additive projection candidate; UI marks them not-exposed honestly |
| Investigation `context_pack` detail | — | P3 | summarized in Portal, not expanded (deliberate bound) |
| Install interactive wizard / profiles matrix | §13 | — | not exercised headless; non-interactive contract proven (`14-packaging-installation.md` §3) |
| Install `uninstall` cleanliness, unicode paths, native Linux | §13 | — | not run — named cells |

## 3. Residual risk notes

- **Auth model scope**: bearer token + HMAC integrated principal cover
  the supported single-user/loopback deployment and the governed
  same-origin proxy pattern. `--allow-remote` stays opt-in with a loud
  warning; exposure beyond loopback was never a supported claim (F007
  classified P1 per the single-user rubric, never P0).
- **Stage ceiling**: no capability is `production_validated`. The AWS
  run was real but bounded (one homologation account, demo API, ≤15
  calls); Bridge/Lens never executed; hosted CI unrun. The manifest
  (`evidence/capability-manifest.json`) records exactly this.
- **PR branches carry unrelated history**: `the-forge@cycle-10-apisentinel`
  contains the full phase-2 line (~90 commits) on top of which the
  adapter commit sits; `forge-portal@cycle-10-sentinel` carries 21
  commits of Obsidian/knowledge work plus the sentinel commit. PR
  targeting must account for this — see `15-pr-plan.md`.
- **Evidence locality**: peer receipts were produced on this Windows
  host against sibling checkouts at recorded SHAs; nothing asserts
  behavior on other hosts or future peer versions.

## 4. Refuted / closed-for-release items (for contrast)

F002 refuted · F001/F007/F009–F015/F018/F022/F023 fixed · F003 closed
by F009's real run · F005 closed by the Portal wave · F006 closed by
the batching fix · F008 partially closed (real bounded AWS) · F016/
F017/F019/F020/F021/F024 recorded with honest states or workarounds.
Final ledger roll-up in `final-report.md` §C.
