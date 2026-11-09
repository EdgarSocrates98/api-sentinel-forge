# E2E Validation Report — api-sentinel-forge v0.1.0-rc.1

Final end-to-end validation pass after Cycles 0–9 (tag `v0.1.0-rc.1`, 440
tests, HEAD `728274f`). Executed 2026-11-09 on Windows 11, venv Python
3.12.13, real installed CLI `api-sentinel.exe` driven by
[`lab/e2e_validate.py`](../../lab/e2e_validate.py) — a deterministic
subprocess runner: fresh workspace under `%TEMP%`, every command real,
`--now`/`--window-*` pinned to the fixture clock (`2026-01-01`), no mocks
(AWS `--simulate` out of scope). Runner exit code: **0**.

**Verdict: PASS — 15/15 steps.**

| # | Step | Result |
|---|------|--------|
| S1 | `install --yes --workspace WS --json` | PASS — workspace + `sentinel.db` created |
| S2 | `services --register` ×3 | PASS — 3 distinct `svc_*` ids |
| S3 | `sources --register jsonl-file` ×3 | PASS — 3 `src_*` ids |
| S4 | `ingest` ×3 + `changes import` | PASS — 8/16/18 accepted, 0 quarantined; manifest 2 imported; re-ingest dedups 8 |
| S5 | `slo define`/`evaluate` | PASS — payments MET (sli 1.0), orders VIOLATED (sli 0.426) |
| S6 | `detect` + dedup re-run | PASS — latency (payments), error+burn (orders), error (checkout); re-runs 0 new |
| S7 | `incidents`/`ack`/`show` | PASS — 4 incidents; ack → TRIAGED + journal |
| S8 | `investigate` | PASS — bounded pack + governed session `delegated` (file seam) |
| S9 | `changes correlate` + `hypotheses add/verify/promote` | PASS — deploy ranked #1 (Δ30 s); VERIFIED via independent re-detect |
| S10 | `handoff doctor` | PASS — `delegated` via `handoff_file`; handoff+receipt on disk; 0 secret patterns |
| S11 | `serve` + httpx `/v1` | PASS — 7 GETs 200; POST investigations 201 (+idempotent replay 200); `/execute` 404 `problem+json` |
| S12 | `backup create` + `restore` → WS2 | PASS — integrity `ok`; services/sources/incidents counts identical |
| S13 | `retention` dry-run + `doctor` | PASS — sane report; `sentinel` self-observability block `available` |
| S14 | restart-recovery | PASS — fresh process reads identical service set |
| S15 | `capabilities --json` | PASS — 13 `available`, 1 `declared` (agentic-investigation) — honest states |

## Method notes

- Fixture ids are remapped sed-style into temp copies (lab README
  procedure): `svc_lab_payments`→payments `svc_*`, `svc_lab_checkout`→
  orders/checkout `svc_*`, `src_lab_jsonl`→that service's `src_*`.
  `declared_source_id` must equal the ingest channel or the record is
  quarantined `origin_mismatch` — 0 quarantined proves the remap.
- `lab-002` (persistent 503) was bound to **orders-api** so checkout-api
  stays a clean regressive-deploy narrative (its seed id is arbitrary).
- `detect` for checkout used the LAB-004 oracle window `00:04–00:10` so
  `first_observed_at=00:04` — the deploy at `00:03:30` precedes the anchor.

## Real output (trimmed)

### S1–S3 workspace + registry

```text
$ api-sentinel install --yes --workspace WS --json   [rc=0]
{"workspace": "C:\\...\\sentinel-e2e-3pstu9g4\\ws", "profile": "local",
 "scope": "project", "portal": false}
$ api-sentinel services --register payments-api --json
{"service_id": "svc_01M6Z2263Y51WKMJVPHR7G51ZT", ...}
services: payments-api svc_01M6Z2263Y51WKMJVPHR7G51ZT
          orders-api    svc_01M6Z226T9H7G607A2S48QMKQX
          checkout-api  svc_01M6Z227KKRV9YEP4RDBQEE4A9
sources:  src_01M6Z2289VT92C674H8X6GZP7F / src_01M6Z22902TBYHD2MMYQCPJZ1Y
          / src_01M6Z229QW4Z467ZT9HZJEZQMN
```

### S4 ingest (id-remapped) + idempotent re-run + manifest

```text
ingest lab-001->payments : {"accepted": 8,  "quarantined": 0}
ingest lab-002->orders   : {"accepted": 16, "quarantined": 0}
ingest lab-004->checkout : {"accepted": 18, "quarantined": 0}
re-ingest lab-001        : {"accepted": 0, "skipped_duplicates": 8,
                            "batch_id": "batch_01M6Z22AETH030DJVBGN8Z2P5G"}
changes import deploy-manifest.json:
  {"imported": 2, "deduplicated": 0, "skipped": 0, "unknowns": [],
   "change_ids": ["chg_297eacbd3f08fab855160cc7", "chg_0695339f1543bef8586b6cfa"]}
```

### S5 SLO

```text
slo evaluate payments 00:00..01:00 ->
  "state": "MET",      "sli": 1.0, "eligible": 951, "quality": "STALE"
slo evaluate orders   00:00..00:10 ->
  "state": "VIOLATED", "sli": 0.4264, "eligible": 999, "bad": 573,
  "budget_remaining": -563.01
```

### S6 detection + dedup

```text
anomalies[payments-api] : 1 -> ['p95_over_latency_target']
anomalies[orders-api]   : 2 -> ['budget_burn_acceleration',
                               'error_ratio_over_limit']   (orders SLO
                               assessment feeds the burn rule — real link)
anomalies[checkout-api] : 1 -> ['error_ratio_over_limit']
re-detect identical args: anomalies=[] deduplicated=[anm_8ddf74c4…,
                          anm_9e85177e…, anm_d2a878b6…, anm_74a5ba43…]
```

### S7 incidents

```text
incidents --json -> 4 incidents (payments 1, orders 2, checkout 1)
checkout incident inc_8f6f5a616b02ec02be0f525f first_observed_at=00:04:00Z
incident ack  -> {"status": "TRIAGED", "acknowledged_at": "2026-11-09T02:26:59Z"}
incident show -> status TRIAGED, 2 journal events (opened + transition)
```

### S8 investigate

```text
{"investigation_id": "inv_01M6Z22RGWPKB31ME6ZQB6WZ29",
 "result": {"pack_id": "pack_d5ee9725b14db0f3236b3382",
            "pack_size_bytes": 4612, "pack_truncated": [],
            "session": {"status": "delegated",
              "session_ref": "...\\agent_sessions\\asr_2c5cfdef…\\session_request.json"}}}
```
Bridge absent → the governed request is emitted as a file-seam envelope
(`delegated`), never a simulated session.

### S9 change intelligence + hypothesis lifecycle

```text
changes correlate -> anchor=2026-01-01T00:04:00Z candidates=1
  top: kind=deploy ref=sha_a11ce99 delta_seconds=30.0 score=1.0
  unknowns: ['changes_after_anchor_excluded:1 — a change after the first
             signal cannot precede it']        # the rollback, named
  proximity_ne_cause disclaimer present
hypotheses add     -> hypothesis_id hyp_6a0969a9…, confidence CORRELATED
hypotheses verify  -> verdict VERIFIED verification_id ver_14ab9bb7…
  receipts: [re_detect -> supporting
             "6 rule(s) evaluated clean on 00:10..00:12"]
hypotheses promote -> confidence VERIFIED, verification_id bound
```

### S10 doctor handoff

```text
{"status": "delegated", "mode": "handoff_file",
 "request_ref": "...\\handoffs\\hnd_h_01M6Z22WCJ…\\doctor_request.json"}
handoffs/+receipts files: 3 — secret-pattern scan (AWS key, private key,
password/token/bearer/api-key): 0 hits
```

### S11 HTTP surface (`serve` on 127.0.0.1, ephemeral port)

```text
GET  /v1/health                 -> 200 application/json
GET  /v1/capabilities           -> 200 application/json
GET  /v1/services               -> 200 application/json
GET  /v1/services/{payments}    -> 200 application/json
GET  /v1/incidents              -> 200 application/json
GET  /v1/incidents/{id}/timeline-> 200 application/json
GET  /v1/integrations/status    -> 200 application/json
POST /v1/incidents/{id}/investigations -> 201
POST same idempotency_key              -> 200 {"replayed": true}
POST /v1/incidents/{id}/execute        -> 404 application/problem+json
```

### S12 backup/restore (LAB-014 essence)

```text
backup create -> ws/backups/backup-20261109T022707Z
  manifest tables: anomalies=4 incidents=4 investigations=2
                   change_events=2 detection_rules=18 checkpoints=3
backup restore --from <dir> --workspace WS2 -> "integrity": "ok"
counts primary : services=3 sources=3 incidents=4
counts restored: services=3 sources=3 incidents=4   IDENTICAL
```

### S13 retention + self-observability

```text
retention (dry-run) -> {"apply": false,
  "candidates": {"observations_minute": 42, ...},
  "protected": {"incidents_open": 4, "evidence_referenced": 45}}
doctor --json -> {"sentinel": {"state": "available", ...},
                  "capabilities": {"available": 13, "declared": 1},
                  "integrations": [api-forge=unverified,
                                   code-atlas=unavailable, ...]}
```

### S14–S15

```text
services --json (new process) -> same 3 svc_* ids
capabilities --json -> 13 available + api.agentic-investigation=declared
                       (honest: Bridge seam not live-proven)
```

## Findings

- **Product bug (UX, Windows-only):** `api-sentinel --help` and non-JSON
  output containing non-cp1252 glyphs (`→` in help text, `≠`/`—` in
  domain strings such as the `proximity≠cause` disclaimer) crash with
  `UnicodeEncodeError` on a legacy cp1252 console — rich's
  `legacy_windows_render` encodes via the console code page. `--json`
  output is unaffected (ASCII-escaped). Workaround used by the runner:
  `PYTHONUTF8=1`. Candidate fix: force UTF-8/`errors="replace"` on the
  CLI's console init.
- **Observed, not a bug:** SLO assessments carry `quality: "STALE"`
  because the evaluated windows sit in the fixture past relative to the
  real clock — the honest freshness marker, exactly per design.
- **Observed:** the uvicorn child exits `rc=1` on `Popen.terminate()`
  (Windows `TerminateProcess`) — expected teardown, clean request log.
- **Observed:** orders-api carries two incidents (error ratio + budget
  burn) — the burn rule legitimately chained off the VIOLATED SLO.

No code paths were modified; the only additions are this report and the
committed runner `lab/e2e_validate.py` (stdlib + httpx, `ruff` clean).
