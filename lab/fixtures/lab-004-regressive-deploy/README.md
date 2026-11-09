# LAB-004 — regressive-deploy (deploy precedes error regression)

Reproduces the Cycle-8 change-intelligence scenario: a CI deploy of
`checkout-api` (`sha_a11ce99`, version 2.7.0) precedes an error-ratio
regression; a later rollback (`2.6.3`) restores a healthy window. The
lab proves bounded agentic investigation produces an *honest* hypothesis —
temporal proximity is evidence, never root cause.

## Contents

- `deploy-manifest.json` — an `api-sentinel/DeployManifest/v1` export
  (e.g. GitHub Actions): the regressive deploy at `00:03:30` plus the
  rollback at `00:09:45`. Imported via `api-sentinel changes import` —
  no GitHub credential required, re-imports dedup.
- `observations.jsonl` — `RuntimeObservation/v1` records:
  - `00:00–00:04` healthy baseline (200s),
  - `00:04–00:10` regressive (~95% 503s — post-deploy),
  - `00:10–00:12` post-fix healthy (rolled back).
- `services.jsonl` — `checkout-api` seed identity (service name, atlas ref).

## What the oracle proves

1. `changes import` lands `deploy` + `rollback` change events;
   re-importing reports `deduplicated`, never duplicates.
2. `detect` over `00:04–00:10` fires `error_ratio_over_limit` → incident
   with `first_observed_at = 00:04` — the anchor.
3. `changes correlate` ranks the deploy first (`delta:30s`, reasons
   listed); the rollback is after the anchor and is *excluded with an
   explicit unknown* — the report always carries `proximity≠cause`.
4. `investigate` emits a bounded `ContextPack` (deploy as change_event
   evidence, disclaimer in unknowns) + a governed session attempt —
   `delegated` via the file seam or `unavailable`, never simulated.
5. `hypotheses add` creates the candidate at `CORRELATED` — never
   `VERIFIED` on proximity alone.
6. `hypotheses verify` re-runs detection on the post-fix window
   (`00:10–00:12`, clean) → `VERIFIED` verdict + receipts; only then can
   `hypotheses promote` set `VERIFIED` — promotion is never self-declared.

## Seeding (manual)

The fixture binds observations and the manifest's service name to
`checkout-api` → `svc_lab_checkout`; register the service under that id
first (`services --register` generates `svc_<ulid>` — seed programmatically
or rename `service_id`/`service` to your registered id), then:

```bash
# service + source rows exist for svc_lab_checkout / src_lab_jsonl
api-sentinel changes import --file lab/fixtures/lab-004-regressive-deploy/deploy-manifest.json --json
api-sentinel ingest --source-id src_lab_jsonl --kind jsonl \
  --file lab/fixtures/lab-004-regressive-deploy/observations.jsonl --json
api-sentinel detect --service-id svc_lab_checkout \
  --window-start 2026-01-01T00:04:00Z --window-end 2026-01-01T00:10:00Z --json
api-sentinel investigate --incident-id <inc_id> --provider claude --cost-limit 2.0 --json
api-sentinel changes correlate --incident-id <inc_id> --json
api-sentinel hypotheses add --incident-id <inc_id> \
  --text "deploy sha_a11ce99 (2.7.0) introduced the 503 regression" \
  --confidence CORRELATED --evidence-for <change_id> --json
api-sentinel hypotheses verify --incident-id <inc_id> \
  --hypothesis-id <hyp_id> \
  --window-start 2026-01-01T00:10:00Z --window-end 2026-01-01T00:12:00Z --json
api-sentinel hypotheses promote --incident-id <inc_id> \
  --hypothesis-id <hyp_id> --verification-id <ver_id> --json
```
