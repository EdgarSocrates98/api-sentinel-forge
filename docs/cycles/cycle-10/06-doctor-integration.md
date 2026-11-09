---
cycle: 10
deliverable: Doctor integration — real in-process execution (F009 closed)
wave: 2
---

# Cycle 10 — Forge Doctor API integration (Wave 2: REAL)

## What changed in Wave 2

- **C10-F009 FIXED.** `integrations/doctor/adapter.py::_run_doctor_typed`
  imported `ProjectContext` from `forge_doctor_api.handoff.context` — stale
  since Doctor 0.3.1 moved it to `forge_doctor_api.core.context`
  (`handoff.context` is now the `doctor://` context-broker and exports no
  `ProjectContext`). The adapter now imports the real 0.3.1 location with a
  legacy `handoff.context` fallback kept as a try/except compatibility path.
- `forge-doctor-api 0.3.1` is installed editable in the Sentinel venv — the
  in-process ACL (`DoctorBoundary`) is the default mode now
  (`integrations --workspace <lab>` reports
  `forge-doctor-api | available | in_process`).

## Real homologation (not a fixture transcript)

`DelegateToDoctor` was run against `lab/fixtures/doctor-project`
(`openapi.yaml` + `app.py`) through the real `DoctorBoundary` in-process
path — the same path `api.doctor-handoff/delegate` uses when routed by
The Forge:

```text
handoff_id: hnd_h_01M70777F9VS1TQ2XNJ8ZD5X8Q
status:     completed            mode: in_process
detail:     delegated in-process through DoctorBoundary
receipt:    rcpt_hnd_h_01M70777F9VS1TQ2XNJ8ZD5X8Q
ingest:     service=doctor-project · 10 findings · 9 unknowns
```

Findings are Doctor's own typed output — e.g. `APISEC001` BOLA candidate on
`GET /orders/{id}` with `STATIC` evidence (openapi.yaml:29) — translated at
the ACL into Sentinel severity/unknowns; nothing synthesized. Evidence:
`evidence/doctor_inprocess_run.json` (full outcome) +
`evidence/doctor_inprocess_receipt.json` (persisted
`transport_state=delivered · execution_state=completed` receipt).

## Delegated ≠ executed — transport honesty preserved

- `in_process`: Sentinel calls `DoctorBoundary` inside its own process;
  `detail` says exactly that. `handoff_file` mode still emits the governed
  `api-sentinel/InvestigationHandoff/v1` envelope under `handoffs/` for a
  separately-run peer and reports `delegated` — never `executed`.
- When `forge_doctor_api` is not importable the status table reports
  `unverified | handoff_file` — the peer is named unconfirmed, not failed.

## Negative battery (14 tests — `tests/integration/test_doctor_negative.py`)

Covers the spec's required matrix, all asserting named states rather than
exceptions or fabricated results:

| Case | Asserted behavior |
|---|---|
| doctor absent (module hidden) | `unavailable` named state |
| version mismatch (API-PROT) | refused `IntegrationRefused` |
| invalid OpenAPI spec | handoff `failed`, Doctor diagnostics preserved |
| truncated handoff JSON | schema gate rejects (`missing field`) |
| timeout (`timeout_seconds=0`) | `failed` + timeout reason, receipt persisted |
| nonzero exit | `failed`, stderr preserved |
| malformed result payload | `failed`, parse error named |
| unresolved `$ref` | Doctor reports it as findings/unknowns — not silent |
| path outside workspace | refused (workspace boundary) |
| missing required field | `InvestigationHandoffV1` rejects (F014) |
| wrong schema literal | `v2` literal rejected |
| delegated-vs-executed | `delegated` never promoted to `executed` |
| truncated envelope shape | partial dict → validation error |
| unknown `kind` string | accepted by model (kind is open), schema stays the contract |

## Contract notes

- Handoff envelope `api-sentinel/InvestigationHandoff/v1` now requires
  `payload` + `constraints` in the Pydantic model — matching the published
  JSON schema's `required` list (F014; the schema is the source of truth,
  the old tolerant-reader asymmetry is closed).
- Doctor `ForgeRequest` protocol v2; the Sentinel-side parser accepts v1/v2.
