# Cycle 5 — Cross-Forge Intelligence

Integrates API Sentinel with Forge Doctor API, Code Atlas and The Forge through
anti-corruption-layer (ACL) adapters. The Sentinel never reimplements Doctor
analysis, Atlas graph construction or The Forge routing — sibling Forges are
consumed only through public, versioned contracts, and every delegation is
optional, deterministic, evidence-preserving and receipted.

## Verification tails

```text
$ python -m pytest -q
242 passed in 7.14s

$ python -m ruff check .
All checks passed!

$ python -m mypy src
Success: no issues found in 70 source files
```

### CLI smoke (fresh workspace `C:\Users\edgar\AppData\Local\Temp\sent_ws`)

```text
$ api-sentinel integrations --json
[{code-atlas-forge: unavailable(file, no export configured)},
 {forge-doctor-api: unverified(handoff_file, forge_doctor_api not importable)},
 {the-forge: available(local_boundary)}]

$ api-sentinel handoff doctor --service-id svc_01M6YT6CCNYGJTTECCSPSPWNPG --json
{"status":"delegated","mode":"handoff_file",
 "request_ref":"...\\handoffs\\hnd_h_01M6YT6G5BC77T3VV51FV9W536\\doctor_request.json", ...}

$ api-sentinel changes --service-id svc_01M6YT6CCNYGJTTECCSPSPWNPG --json
[]   # valid JSON; a recorded change lists as an entry

$ SentinelBoundary().handle({op:execute, capability:api.service-inventory, action:list})
status=ok  findings=[{id: svc_…, title:"service orders", …}]

$ python -m theforge_apisentinel.record          # adapter live-catalog verification
catalog verification: 5/5 supported -> catalog_verification.json
```

## Acceptance criteria

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Doctor ACL translates Sentinel evidence → Doctor's handoff contract; accepts returned findings/unknowns without reclassification or provenance loss | ✅ | `integrations/doctor/translate.py` + `adapter.py`; `handoff doctor` emits `InvestigationHandoffV1`→`ForgeRequest`; `tests/unit/test_integrations_translate.py`, `tests/contract/test_contracts.py` |
| 2 | Atlas ACL consumes a public structural contract; structural edges kept separate from observed runtime edges | ✅ | `contracts/v1/atlas_export.schema.json`, `integrations/atlas/`; correlation output keys `structural_edges` vs `observed_edges` distinct; `tests/unit/test_integrations_atlas.py` |
| 3 | Provenance-bearing identity translation; unresolved identities stay `UNKNOWN` | ✅ | `integrations/correlate.py` — no `code_atlas_identity` external ref ⇒ unresolved, an explicit unknown, never a name-match guess |
| 4 | Strict `api-sentinel/InvestigationHandoff/v1` → target Forge request contract | ✅ | `doctor/translate.py` emits Doctor `ForgeRequest` v2; `parse_envelope` rejects unknown schema via `ContractError` |
| 5 | Unknowns preserved end-to-end | ✅ | `unknowns` field carried through handoff, correlate, boundary result; ingest test preserves a Doctor `UnknownFact` verbatim |
| 6 | Redacted receipt for every attempted delegation | ✅ | `integrations/receipts.py` → `receipts/`; `api_sentinel.infrastructure.security.redact` masks secrets as `[REDACTED]`; redaction test asserts raw token absent |
| 7 | Bounded per-integration timeouts with named terminal timeout states | ✅ | `handoff doctor --timeout-seconds`; `HandoffStatus.timed_out`; thread-executor bound in `doctor/adapter.py`; `tests/unit/test_integrations_translate.py` timeout case |
| 8 | Named unavailability/refusal states when integrations absent | ✅ | `integrations/status.py` → `unavailable`/`unverified`/`available`; Atlas adapter returns explicit refusal when no export configured |
| 9 | Provider/consumer contract tests | ✅ | `tests/contract/test_contracts.py` (envelope round-trips, strict version rejection), `tests/contract/test_theforge_adapter.py` (snapshot ↔ live catalog) |
| 10 | LAB-003 dependency-cascade proof: observed edge + structural context + incident + honest uncertainty, never causal certainty | ✅ | `lab/fixtures/lab-003-dependency-cascade/`; correlate emits `suspected_origin: hypothesized`, `hypotheses`, `unknowns`; `tests/integration/test_cycle5_integrations.py` |
| 11 | The Forge adapter declares capability mappings + records live catalog verification | ✅ | `adapters/apisentinel/` (shipped for review, copied into the-forge at registration); `catalog.py` CapabilitySpecs; `record.py` → `catalog_verification.json` 5/5 supported |
| 12 | `forge-knowledge/api-sentinel-forge.json` operational facts only, `capabilities_source="runtime_discovery"` | ✅ | pre-existing file reviewed; no ownership of Doctor analysis / Atlas graphs / Forge routing; `capabilities_source` unchanged |
| 13 | Typed Sentinel boundary `describe`/`health`/`capabilities`/`execute`; discovery ≠ verification ≠ executability | ✅ | `integrations/the_forge/boundary.py`; `handle()` returns `theforge/ExecutionResult/v1`; boundary tests assert describe/health don't claim execute |

## Capability ids

Confirmed available (version `0.2.5`): `api.service-inventory`, `api.runtime-observation`,
`api.slo-assessment`, `api.anomaly-detection`, `api.incident-investigation`,
`api.evidence-storage`, `api.telemetry-source-registration`,
`api.persistence-recovery`.

Changed to available this cycle: `api.deployment-correlation`, `api.doctor-handoff`.

Declared (future): `api.portal-operations`, `api.aws-readonly-observability`.

## Files

- Domain/storage: `src/api_sentinel/domain/models.py` (`ChangeKind`, `ChangeEvent`,
  `HandoffStatus`, `ExternalHandoff`, `DependencyObservation`),
  `src/api_sentinel/domain/__init__.py`, `migrations/0005_integrations.sql`,
  `src/api_sentinel/application/ports.py`,
  `src/api_sentinel/infrastructure/storage/repositories.py`.
- Contract: `src/api_sentinel/contracts/v1/models.py` (`AtlasExportV1`),
  `src/api_sentinel/contracts/v1/__init__.py`,
  `contracts/v1/atlas_export.schema.json`.
- Integrations: `src/api_sentinel/integrations/{__init__,errors,receipts,status,correlate,service}.py`,
  `integrations/atlas/{__init__,models,port,file_adapter}.py`,
  `integrations/doctor/{__init__,translate,adapter}.py`,
  `integrations/the_forge/{__init__,handoff,boundary}.py`.
- CLI/capabilities: `src/api_sentinel/cli/__init__.py`,
  `src/api_sentinel/capabilities.py`.
- The-forge adapter (shipped for review, copied at registration):
  `adapters/apisentinel/` — `pyproject.toml`, `README.md`,
  `src/theforge_apisentinel/{__init__,__main__,catalog,record}.py`,
  `native_surface.json`, `catalog_verification.json`.
- Lab: `lab/fixtures/lab-003-dependency-cascade/` —
  `README.md`, `atlas-export.json`, `services.jsonl`, `observations.jsonl`,
  `dependencies.jsonl`, `changes.jsonl`.
- Tests: `tests/unit/test_integrations_{atlas,boundary,status,translate}.py`,
  `tests/integration/test_cycle5_integrations.py`,
  `tests/contract/test_contracts.py` (cycle-5 envelope/contract cases),
  `tests/contract/test_theforge_adapter.py`.
- Config: `pyproject.toml` (`adapters/**` → `T20` per-file-ignore — the adapter
  is a forge/v1 stdio bridge; `print` is the wire).

## Mismatches & honest gaps

- **the-forge adapter location.** The spec asks for the adapter *in* the
  the-forge repo (`adapters/apisentinel`), but the task forbids modifying
  the-forge. Resolution (same lifecycle as `forge-knowledge/`): the adapter is
  **shipped here for review** under `adapters/apisentinel/` and copied into the
  the-forge repo at registration. It is a real, separate stdlib-only
  distribution with `CapabilitySpec`s, a recorded `native_surface.json`, and a
  `catalog_verification.json` (5/5 supported) — not a stub.
- **Atlas unavailable by default.** No `integrations.atlas.export` configured ⇒
  named `unavailable` (correct, never fabricated); correlation then runs on
  observed edges only and reports the structural-context unknown.
- **Doctor unverified in handoff_file mode.** `forge_doctor_api` not importable
  in this env ⇒ `unverified`; the emitted `doctor_request.json` envelope is for
  a separately-run Doctor, which returns a bundle via `handoff doctor --ingest`.
- **`_shell.py` not vendored.** The the-forge adapters vendor a byte-identical
  `_shell.py` (1337 lines, a test enforces identity). Ours is a thin stdio
  bridge because `SentinelBoundary` already *is* the forge/v1 surface
  (`handle()` produces a `Response`/`ExecutionResult`). At registration the
  vendored `_shell.py` may be adopted if the core requires its staging/replay
  helpers; flagged honestly rather than copied blind.
- **Live catalog = Sentinel's own runtime catalog.** The five capability ids
  were verified against the boundary's live `describe()`/`capabilities()` output
  (5/5 `supported`) and recorded in `catalog_verification.json` — the spec's
  "verification of those ids against the live capability catalog".
- **LAB-003 is hypothesized, not verified.** With only temporal + structural
  evidence the report sets `suspected_origin: hypothesized` and carries
  `unknowns` — no causal certainty is claimed.

Commit: see `git log -1` (cycle commit, message
`Cycle 5: cross-Forge intelligence via ACL adapters`).
