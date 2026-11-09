# Cycle 10 — Revalidated findings ledger (Wave 0)

Ledger in the prompt §4 format. Severity rubric: **P0** critical security /
corruption / improper execution / structural failure; **P1** important product,
integration or reliability defect; **P2** evolution/performance/UX; **P3**
low-risk improvement. Unavailable *optional* integrations are classified by the
functional commitment actually declared — never inflated to P0.

Audited at `cycle-10 @ 29375e3f` (api-sentinel-forge), siblings at the SHAs in
`evidence/baseline.json`. Every finding cites real source or command output.

---

## C10-F001 — README stale at "Cycle 1"

```text
id: C10-F001
severity: P3
repository: api-sentinel-forge
file: README.md:10
symbol: status badge line
contract: none (documentation)
root_cause: README frozen at Cycle-1 wording while CHANGELOG.md:11 declares
  "Release candidate — cycles 0–9 consolidated" and tag v0.1.0-rc.1 exists.
reproduction: `grep -n "cycle" README.md` → line 10 "Status: **Cycle 1 — Core,
  Identity & Persistence**"; lines 48-53 advertise only Cycles 2–4 commands.
impact: cosmetic/incorrect project state for any reader; no functional risk.
evidence: README.md:10 vs CHANGELOG.md:11-50; commit 29375e3f.
owner: sentinel maintainer
remediation: refresh README status + feature table to the 0–9 consolidated RC
  surface (Wave 1+, docs-only).
negative_tests: markdown lint / README-vs-changelog consistency check.
compatibility: none.
status: CONFIRMED (still divergent at HEAD)
```

## C10-F002 — cli entrypoint vs manifest — REFUTED

```text
id: C10-F002
severity: none (closed — no defect)
repository: api-sentinel-forge
file: pyproject.toml:60-64 · forge.agentic.json:5 · src/api_sentinel/cli/__init__.py:55,1754
symbol: api-sentinel console script; cli_entry
contract: forge/SpecialistAgenticManifest/v1 cli_entry must be importable
root_cause: suspected divergence between "api_sentinel.cli:main" (script) and
  "api_sentinel.cli:app" (manifest) — investigation shows they are two valid
  contracts: `main()` (cli/__init__.py:1754) reconfigures utf-8 then
  sys.exit(_dispatch()) → app(); `app` (line 55) is the Typer object hosts
  import.
reproduction: wheel built via `uv build`; installed into a fresh venv under
  %TEMP%; `api-sentinel.exe doctor --json` works AND
  `python -c "from api_sentinel.cli import app"` works.
impact: none.
evidence: evidence/installation-results.json — clean-wheel probes all pass.
owner: —
remediation: none required; the pyproject comment already documents the split.
negative_tests: installation-results.json records both import probes.
compatibility: unchanged.
status: REFUTED — no effective divergence of execution or declaration.
```

## C10-F003 — Doctor handoffs emitted, never executed/consumed

```text
id: C10-F003
severity: P1
repository: api-sentinel-forge (+ forge-doctor-api for the consumer side)
file: src/api_sentinel/integrations/doctor/adapter.py:147-278
symbol: DoctorACL._dispatch / _delegate_file / _run_doctor_typed
contract: api-sentinel/InvestigationHandoff/v1 → forge_doctor_api ForgeRequest
  (protocol v2) → ApiHandoffBundle
root_cause: `forge_doctor_api` is NOT installed in the sentinel .venv
  (importlib probe: find_spec → None), so mode auto-resolves to handoff_file:
  envelopes are written under <ws>/handoffs/<id>/ and status is `delegated`.
  No forge-doctor-api CLI/file-ingest entrypoint consumes doctor_request.json —
  its consumption surfaces are DoctorBoundary (in-process) and the MCP stdio
  server (handoff/mcp_server.py), neither wired to the file transport.
  Worse: the in-process path itself is broken — see C10-F009.
reproduction: `api-sentinel handoff doctor --service-id X --json` →
  {"status": "delegated", "mode": "handoff_file"} (e2e S10 verbatim in
  evidence/baseline-tests.txt); `find_spec("forge_doctor_api")` → None.
impact: the "Doctor diagnoses Sentinel investigations" story is unproven —
  every receipt records emission, never a returned bundle.
evidence: e2e S10 output; adapter.py:268-278 (status="delegated" on emit);
  forge-doctor-api repo inspection (no file consumer);
  evidence/contract-conformance.json → doctor_handoff.
owner: sentinel + doctor maintainers
remediation: (a) fix the stale ProjectContext import (C10-F009) and exercise
  the real in-process run against a doctor wheel install; (b) or add a
  file-ingest boundary on the doctor side; keep `delegated` honest until then.
negative_tests: in-process run against real forge_doctor_api 0.3.1;
  malformed doctor_request.json must fail loudly.
compatibility: ForgeRequest v2 emission is field-correct (translate.py:32-46
  mirrors protocol.py:124-167) — only the transport/consumer is missing.
status: CONFIRMED — emission real, consumption never executed.
```

## C10-F004 — Agent Bridge session: delegated ≠ executed

```text
id: C10-F004
severity: P1
repository: api-sentinel-forge
file: src/api_sentinel/integrations/bridge/adapter.py:40-134
symbol: FileAgentSession / UnavailableAgentSession / bridge_session_from_config
contract: AgentSessionRequest → forge-agent-bridge governed session
root_cause: the ONLY transports are file-emission (`delegated`) and the named
  `unavailable`. `bridge_importable()` exists (adapter.py:35-37) and is used by
  status reporting (status.py:108) but bridge_session_from_config (lines
  108-134) never calls it — there is no execution path even if
  forge_agent_bridge were installed. `delegated` means "envelope on disk",
  per the adapter's own docstring.
reproduction: inspect bridge_session_from_config — modes {file, unavailable}
  only; `investigate` in e2e S8 emits agent_sessions/session_request.json.
impact: agentic investigation cannot prove an external provider session ran;
  api.agentic-investigation stays DECLARED (honest) but the homologation gap
  the prompt flags is structural, not creds-related.
evidence: adapter.py read-through; status.py:108; e2e S8/S15 output.
owner: sentinel + bridge maintainers
remediation: define the bridge consumption contract (does forge-agent-bridge
  read session_request.json? verify), then wire an executed-session transport
  or an explicit receipt-ack loop.
negative_tests: absent bridge → unavailable; replayed request → dedup (already
  covered).
compatibility: request envelope redacted at write (adapter.py:62) — ok.
status: CONFIRMED — no executed-session path exists.
```

## C10-F005 — Portal /api-operations does not reach the Sentinel

```text
id: C10-F005
severity: P1
repository: forge-portal (consumer) + product-forge (backend) + api-sentinel-forge
file: forge-portal/src/app/pages/api-operations/api-operations.page.ts:470,502-553
symbol: ApiOperationsPage → PfApi → product-forge PortalApi
contract: forge/PortalApi/1 envelope vs sentinel application/problem+json
root_cause: the page injects `PfApi` (line 470) which calls
  `/products`, `/products/{id}/apis`, `/products/{id}/health` on the
  product-forge engine (pf-api.service.ts:256-271+). Page docstring (lines
  15-19): "decision surface over Product Forge's real API inventory". Zero
  sentinel references in src/app (grep verified). The sentinel /v1 surface
  (api-sentinel serve :8080, X-Sentinel-Role, problem+json) is not consumed by
  anything — and its envelope is structurally incompatible with
  forge/PortalApi/1 anyway.
reproduction: grep -rn "sentinel" forge-portal/src/app → only an unrelated
  "release-sentinel" template name; read api-operations.page.ts.
impact: no Portal↔Sentinel operational link exists; the CLI help text "for the
  Forge Portal" (serve command) describes intent, not reality.
evidence: page source; pf-api.service.ts endpoints; product-forge
  _forge_portal.py:38; evidence/contract-conformance.json → portal_api.
owner: portal + sentinel maintainers
remediation: either expose a PortalApi-shaped adapter over sentinel /v1, or
  teach the portal a second backend; wire health/services/incidents.
negative_tests: envelope mismatch must fail visibly, never silently.
compatibility: requires contract bridge — not a config change.
status: CONFIRMED — DEFERRED to Wave 3 (cross-repo contract bridge:
  forge-portal + product-forge consumers; out of this repo's Wave-1
  security scope).
```

## C10-F006 — Ingestion N+1 under dispersed windows

```text
id: C10-F006
severity: P2
repository: api-sentinel-forge
file: src/api_sentinel/ingestion/pipeline.py:150,178-186 ·
      infrastructure/storage/repositories.py:425,480-498
symbol: IngestionPipeline.ingest; _ObservationRepo.get / .overlapping_sources
contract: none (internal performance)
root_cause: two per-item/per-key query loops: (a) observations.get per
  observation for dedup — 10,000 SELECTs at n=10k; (b) overlapping_sources per
  distinct (service,window) — 2,880 SELECTs at 1440 windows/chunk × 2 chunks.
reproduction: stock bench 10k sequential → 1075.6 obs/s; instrumented
  dispersed-window run (1440 windows/chunk, repo-level call counters) →
  934.9 obs/s, chunk_seconds [3.87, 5.684] growing with table size.
  cycle-09 report documents ~200 obs/s at this shape on the older harness.
impact: throughput degrades with window dispersion and table growth; bounded
  but real — the overlap check can be batched (one range query) and the dedup
  get can be keyed-set lookup.
evidence: evidence/performance-before.json (verbatim bench + query counts).
owner: sentinel maintainer
remediation: batch overlap check into a single windowed query per service;
  replace per-item get with set-membership over pre-fetched observation_ids.
negative_tests: re-run instrumented bench; assert query counts drop
  (observations_get → 0 batched; overlapping_sources → ≤ #services).
compatibility: internal only; results must stay identical (dedup + overlap
  semantics preserved).
status: CONFIRMED — DEFERRED to Wave 4 (P2 performance; needs a
  benchmarked before/after run, not a Wave-1 drive-by).
```

## C10-F007 — X-Sentinel-Role trusted verbatim

```text
id: C10-F007
severity: P1
repository: api-sentinel-forge
file: src/api_sentinel/api/deps.py:117-144 · cli/__init__.py:1689-1739
symbol: require_scope; serve
contract: /v1 authorization model (docs/07 placeholder)
root_cause: effective role = raw X-Sentinel-Role header or workspace default —
  no token, signature or identity proof; ROLE_SCOPES grants by
  self-declaration; ungated reads return 200 to any role string including
  "intruder" (asserted in test_authz_matrix.py:225-229).
reproduction: header {"X-Sentinel-Role": "admin"} reaches admin-scope routes;
  documented placeholder — loopback bind default, --allow-remote is opt-in
  with a loud warning.
impact: zero effective authorization on a mutating API; safe ONLY under the
  supported single-user loopback deployment (a local attacker already owns
  the sqlite file). Becomes P0-equivalent under --allow-remote exposure.
evidence: deps.py:129; test matrix; serve bind guard; evidence/security-results.json.
owner: sentinel maintainer
remediation: Phase-1 per prompt §5.1 — real principal/token exchange; keep
  loopback default; fail-closed on unknown principals.
negative_tests: forged/absent/unknown role → 401/403 on every scoped route;
  remote bind without token refused.
compatibility: breaking change to the /v1 auth contract — version or dual-run
  required.
status: FIXED — Wave 1. Local bearer token (secrets.token_urlsafe(32),
  compare_digest, env>stdin>file precedence, optional TTL rotation) +
  HMAC-signed integrated principal (±300 s skew, single-use nonce,
  workspace match). Self-declared role → 401; live smoke verified
  (401/401-spoof/200). In-process ApiContext(auth=None) seam retained
  for TestClient only, unreachable via serve. Evidence:
  evidence/security-results.json; docs/07 §Cycle-10.
```

## C10-F008 — External homologations still BLOCKED

```text
id: C10-F008
severity: P1
repository: api-sentinel-forge (+ all peers)
file: docs/cycles/cycle-09-report.md:171-184
symbol: "Non-executable homologation items"
contract: peer consumption + AWS + hosted CI + portal E2E
root_cause: environment constraints, not code: no AWS credentials in dev; peers
  are out-of-repo with no consumption path proven (F003-F005); hosted CI first
  runs on push; portal UI lives in forge-portal.
reproduction: cycle-09 list re-verified at cycle-10 HEAD — every item still
  holds; sentinel .venv has boto3 but no real account was exercised; all peer
  modules absent (find_spec None for 7 sibling packages).
impact: "PRODUCTION_VALIDATED" is unreachable for the federated claims; the
  stage matrix must keep them at their proven rungs.
evidence: baseline.json optional_modules_in_venv; cycle-09 report verbatim;
  doctor --json integration states (all peers unverified/unavailable).
owner: Edgar (credentials) + wave owners (peer homologation)
remediation: per-repo homologation runs in Waves 1+; record receipts per peer.
negative_tests: each homologation must produce a verifiable receipt file.
compatibility: none.
status: CONFIRMED — unchanged, still blocked.
```

---

## New findings surfaced by this audit

## C10-F009 — in-process Doctor import is stale (ProjectContext moved)

```text
id: C10-F009
severity: P1
repository: api-sentinel-forge
file: src/api_sentinel/integrations/doctor/adapter.py:216-224
symbol: _run_doctor_typed imports
contract: forge_doctor_api.handoff.context.ProjectContext (stale) vs
  forge_doctor_api.core.context.ProjectContext (real, doctor 0.3.1)
root_cause: DoctorBoundary's ProjectContext moved from handoff/context.py to
  core/context.py in the doctor repo; handoff/context.py now contains the
  doctor:// context-broker and re-exports nothing named ProjectContext
  (grep: 0 hits).
reproduction: in the doctor repo's own .venv:
  `from forge_doctor_api.handoff.context import ProjectContext`
  → ImportError; `from forge_doctor_api.core.context import ProjectContext` → OK.
  At runtime this lands in adapter.py:195-201 `except Exception` →
  status="failed" — the in-process mode would never reach `completed`.
impact: even with forge_doctor_api installed, the real-execution path fails —
  the gap in C10-F003 is deeper than "module not installed".
evidence: live import probe (baseline.json → forge-doctor-api.boundary_probe);
  doctor source grep.
owner: sentinel maintainer
remediation: import ProjectContext from forge_doctor_api.core.context (or from
  the package's public __init__ if re-exported) and add a real in-process
  homologation test.
negative_tests: run _run_doctor_typed against doctor 0.3.1 wheel → completed;
  assert no ImportError path.
compatibility: doctor-side public path is core.context at 0.3.1.
status: FIXED — Wave 2. `adapter.py:_run_doctor_typed` now imports
  `forge_doctor_api.core.context.ProjectContext` with a legacy
  `handoff.context` try/except fallback. REAL in-process homologation
  run against `lab/fixtures/doctor-project` (forge-doctor-api 0.3.1
  installed editable in the Sentinel venv): status=completed, 10
  findings / 9 unknowns, receipt rcpt_hnd_h_01M70777F9VS1TQ2XNJ8ZD5X8Q —
  evidence/doctor_inprocess_run.json + doctor_inprocess_receipt.json.
```

## C10-F010 — Boundary executor gap: 5 of 7 declared pairs refuse

```text
id: C10-F010
severity: P1
repository: api-sentinel-forge
file: src/api_sentinel/integrations/the_forge/boundary.py:44-52,188-194,379-382
symbol: _ACTIONS vs _EXECUTORS
contract: theforge/ExecutionResult/v1 via forge/v1 execute
root_cause: describe() advertises 7 capability/action pairs (service-inventory,
  runtime-observation, slo-assessment, anomaly-detection,
  incident-investigation, deployment-correlation, doctor-handoff) but
  _EXECUTORS only maps 2 — service-inventory/list and
  runtime-observation/summarize. The other 5 are AVAILABLE in capabilities.py
  so they pass the status gate, then die at line 189-194 with
  APISENT-NO-EXECUTOR.
reproduction: SentinelBoundary().handle({op:"execute",
  payload:{capability:"api.slo-assessment", action:"evaluate"}}) →
  refused APISENT-NO-EXECUTOR.
impact: The Forge can route to the Sentinel but 71% of the advertised
  executable surface is a named refusal — honest, but INTEGRATED covers only
  2/7 pairs.
evidence: boundary.py read-through; _ACTIONS/_EXECUTORS table; capabilities.py
  AVAILABLE list.
owner: sentinel maintainer
remediation: implement executors for the remaining pairs (they map to existing
  use cases: EvaluateSlo, RunDetection, ListIncidents, Correlate,
  DelegateToDoctor) or downgrade describe() to the real surface.
negative_tests: each new executor returns a conformant ExecutionResult dict;
  unknown pairs still refuse.
compatibility: additive — refusals become executions.
status: FIXED — Wave 2. All 8 declared pairs now have real executors
  wired to existing use cases (EvaluateSlo, RunDetection, ListIncidents,
  ShowIncident, Correlate, DelegateToDoctor) — `incident-investigation`
  gained the catalog-declared `show` action. Routed through the REAL
  the-forge runtime (registry refresh → ask → ExecutionResult →
  receipt): run ids 20261109T131310Z-34446ffc (anomaly-detection),
  -21329c86 (incident list), -e7974201 (doctor-handoff),
  -0e57520c (slo unpinned selection), -004a614a (correlate) —
  evidence/theforge/runs/*/receipt.json.
  Real contract gates surfaced en route — C10-F015/F016/F018.
```

## C10-F011 — api-sentinel/AtlasExport/v1 has no producer

```text
id: C10-F011
severity: P1
repository: api-sentinel-forge (consumer) · code-atlas-forge (missing producer)
file: src/api_sentinel/integrations/atlas/file_adapter.py ·
      code-atlas-forge/src/** (zero matches)
symbol: AtlasExportV1 expected_schema
contract: api-sentinel/AtlasExport/v1 (contracts/v1/models.py:168)
root_cause: FileStructuralContext.load requires an AtlasExport/v1 file, but
  code-atlas-forge 1.1.1 emits no such schema — grep for "AtlasExport" and
  "api-sentinel" across code-atlas src/ returns zero hits. Lab fixture
  lab/fixtures/atlas-export.json is sentinel-authored, masking the gap in tests.
reproduction: grep -rn "AtlasExport\|api-sentinel" code-atlas-forge/src → none.
impact: structural correlation can only ever run on sentinel-authored
  fixtures — the real Atlas seam is unbuilt on the producer side.
evidence: grep output; file_adapter.py:18-70; code-atlas contract list in
  baseline.json.
owner: code-atlas + sentinel maintainers
remediation: define the export on the Atlas side (or adopt an existing Atlas
  export shape and translate at the ACL), then homologate end-to-end.
negative_tests: real code-atlas export feeds Correlate; stale fixture removed
  from the proof path.
compatibility: producer-side new contract.
status: FIXED — Wave 2 (Sentinel-side seam, no Atlas change needed).
  Code Atlas 1.1.1's real export surface is its SQLite index
  (`atlas.db`: domain_entities / domain_entity_occurrences /
  domain_relations / evidence / snapshots / repositories — CLI commands
  wrap it; no CLI emits a Sentinel contract). New
  `integrations/atlas/store_adapter.py::DbStructuralContext` consumes
  the store READ-ONLY (`mode=ro`) selected via
  `integrations.atlas.db_path` + `repository`/`snapshot_id` —
  resolution by exact identity_key/qualified_name (never name-only);
  edges keep Atlas's own type/resolution_status/confidence_class/
  resolver/evidence_ids verbatim. Real run: `code-atlas index
  lab/fixtures/atlas-repo --domains api` → snapshot 11 (4 entities,
  9 domain relations incl. 2 external + 1 unresolved CALLS_ENDPOINT);
  `correlate` resolved service.app#app → 3 structural CONTAINS edges —
  evidence/atlas_correlate_run.json + evidence/atlas/. The
  AtlasExport/v1 FILE seam remains supported for pre-exported
  manifests; the fixture stays a fixture (tests), not the proof path.
```

## C10-F012 — `mcp serve` declared in manifest, command absent

```text
id: C10-F012
severity: P1
repository: api-sentinel-forge
file: forge.agentic.json (mcp_command) · src/api_sentinel/cli/__init__.py
symbol: mcp_command vs CLI command table
contract: forge/SpecialistAgenticManifest/v1 mcp_command must resolve
root_cause: manifest declares ["{cli}","mcp","serve"] but `api-sentinel mcp`
  does not exist — `api-sentinel mcp serve` → "No such command 'mcp'"; grep -i
  mcp over cli/__init__.py → 0 hits; no MCP server module in src/.
reproduction: .venv/Scripts/api-sentinel.exe mcp serve → error (verbatim in
  baseline-tests.txt).
impact: any host adapter honoring mcp_command fails at spawn; manifest
  over-promises.
evidence: command output; forge.agentic.json.
owner: sentinel maintainer
remediation: implement `api-sentinel mcp serve` (the mcp extra is already in
  the venv) or remove mcp_command from the manifest until Wave N delivers it.
negative_tests: mcp serve starts stdio server; manifest-vs-CLI drift check.
compatibility: additive if implemented; removing the key is honest.
status: FIXED — Wave 1. src/api_sentinel/mcp_server.py exposes a
  read-only stdio MCP server (installed `mcp` package, optional-import
  guarded, no new required dep): sentinel_list_services,
  sentinel_capabilities, sentinel_health, sentinel_list_incidents.
  `api-sentinel mcp serve` resolves the manifest's mcp_command;
  covered by tests/integration/test_mcp_server.py.
```

## C10-F013 — lint debt: 2 ruff errors in scripts/cov_core.py

```text
id: C10-F013
severity: P3
repository: api-sentinel-forge
file: scripts/cov_core.py:4
symbol: open() call
contract: none
root_cause: PTH123 + SIM115 — `open()` instead of Path.open() + no context
  manager in a coverage helper script.
reproduction: `.venv/Scripts/python.exe -m ruff check .` → Found 2 errors.
impact: lint gate noise only; not product code.
evidence: evidence/baseline-tests.txt ruff tail.
owner: sentinel maintainer
remediation: `Path(sys.argv[1]).open()` inside a with-block.
negative_tests: ruff clean.
compatibility: none.
status: CONFIRMED — trivial.
```

## C10-F014 — InvestigationHandoffV1: defaults vs required (asymmetric validation)

```text
id: C10-F014
severity: P3
repository: api-sentinel-forge
file: src/api_sentinel/contracts/v1/models.py (InvestigationHandoffV1) vs
      contracts/v1/investigation_handoff.schema.json "required"
symbol: payload / constraints fields
contract: api-sentinel/InvestigationHandoff/v1
root_cause: the Pydantic model declares `payload` and `constraints` with
  default factories (empty dict / default HandoffConstraints) while the
  published JSON schema lists both in "required". The typed consumer
  therefore accepts envelopes the schema gate refuses — asymmetric
  validation surfaced by the Wave-1 truncated-object negative test.
reproduction: strip payload+constraints from a valid handoff dict →
  Draft202012Validator rejects, InvestigationHandoffV1.model_validate
  accepts with synthesized defaults.
impact: low — tolerant reader on the consumer side; but a handoff that
  fails the schema gate can still be materialized in-process, so the
  schema is not the single source of truth for requiredness.
evidence: tests/contract/test_schema_compat.py truncated-object case
  (adjusted to delete fields without defaults); model Field
  declarations.
owner: sentinel maintainer
remediation: either drop the default factories (make schema+model
  agree on required) or remove the fields from the schema's required
  list — decide in Wave 2; making them required may break emitters
  that rely on the defaults.
negative_tests: truncated-object negative now asserts both layers
  refuse when a no-default field is removed.
compatibility: changing requiredness either way is a contract-visible
  decision; needs the contract review lane, not a silent edit.
status: FIXED — Wave 2, decision recorded: `payload` and `constraints`
  are REQUIRED in the Pydantic model (the published JSON schema lists
  both in "required" — the schema is the single source of truth for
  requiredness). Wire output is unchanged (emitters always serialized
  both); only the tolerant-reader asymmetry is closed. Tests:
  tests/integration/test_doctor_negative.py::test_missing_contract_
  field_rejected + schema-compat truncated-object cases.
```

---

## Wave-2 findings (surfaced by the real-integration work)

## C10-F015 — Sentinel manifest emitted `default_action: null`

```text
id: C10-F015
severity: P1
repository: api-sentinel-forge
file: src/api_sentinel/integrations/the_forge/boundary.py:82-94
symbol: describe() capability table
contract: theforge/ForgeManifest/v1 — `default_action: str`, member of
  `actions`
root_cause: describe() listed EVERY catalog capability, emitting
  `actions: []` + `default_action: null` for CLI-scoped capabilities
  (telemetry-source-registration, evidence-storage,
  persistence-recovery, portal-operations, aws-readonly-observability,
  change-intelligence). the-forge's live manifest validation rejected
  the handshake: `$.payload.capabilities[1].default_action: expected
  string, got NoneType`.
reproduction: register api-sentinel-forge in the-forge and run
  `theforge registry refresh` → provider state=invalid with the exact
  field path.
impact: the provider could not register at all — the advertised
  surface violated the consumer's schema, not just honesty rules.
status: FIXED — Wave 2. describe() declares ONLY pairs with real
  executors (a capability without a routable pair stays visible via
  the `capabilities` op, never padded with a fake action); manifest
  limitation records the rule. Live registry refresh now reports
  state=ready with 7 capabilities.
```

## C10-F016 — the-forge `provider check` probes empty workspaces

```text
id: C10-F016
severity: P2
repository: the-forge (harness behavior) · api-sentinel-forge (reporting)
file: the-forge/src/theforge/conformance.py:_execute_payload
contract: conformance execute probes always run with
  task.workspace_root = a fresh TemporaryDirectory
root_cause: the conformance battery intentionally probes every declared
  pair against an empty scratch workspace. A workspace-backed provider
  (the Sentinel requires an initialized `.api-sentinel` store) cannot
  open its db there and — correctly — refuses APISENT-WORKSPACE-
  UNAVAILABLE rather than fabricating an empty result or silently
  creating state in a probe dir.
impact: `theforge provider check` reports execute/handoff as FAIL for
  any storage-backed provider even when every protocol gate passes
  (describe/features/health/refusals/malformed-protocol/replay-
  determinism/timeout/producer-identity all green). The honest named
  refusal is the correct behavior; real execution is proven by the
  routed `ask` runs against an initialized workspace
  (evidence/theforge/runs/).
status: CONFIRMED — recorded, no code change: auto-creating a
  workspace inside a read probe would be a worse side effect than the
  named refusal. Recommendation to the-forge: an execute probe tier
  that accepts a provider-supplied fixture root (deferred, peer-side).
```

## C10-F017 — forge/v1 has no parameter channel

```text
id: C10-F017
severity: P2
repository: the-forge (wire contract) · api-sentinel-forge (adaptation)
file: the-forge/src/theforge/forger/orchestrator.py:369-381
contract: TaskSpec.constraints carries only plan-node metadata on a
  plain `ask` — service_id / window bounds cannot travel to a provider.
root_cause: AskRequest → TaskSpec populates constraints={} for direct
  asks; only plan nodes receive {plan:{run,node}}.
impact: service-scoped execute pairs (anomaly-detection,
  deployment-correlation, doctor-handoff) cannot receive their natural
  scope via the wire.
status: WORKED AROUND — Wave 2, honest rule documented in boundary:
  explicit param > single-service workspace scope > named ambiguity
  ("service_id required: N services registered — never guessed").
  Zero-service scope refuses APISENT-MISSING-PARAM. No name matching,
  no intent-string parsing, no target-path abuse.
```

## C10-F018 — Sentinel evidence refs dangle under ExecutionResult validation

```text
id: C10-F018
severity: P1
repository: api-sentinel-forge
file: src/api_sentinel/integrations/the_forge/boundary.py executors
contract: theforge/ExecutionResult/v1 — every finding.evidence_ids
  entry must reference an Evidence record declared in the same result.
root_cause: anomaly/incident findings carried Sentinel-side refs
  (obs:…, src:…, last_window_end:…) verbatim but the result emitted no
  matching Evidence records → real transport validation rejected:
  FORGE-RESULT-DANGLING-EVIDENCE findings[0].evidence_ids[0].
reproduction: `theforge ask --capability api.anomaly-detection
  --action run` → status=provider_failure with the exact field path.
impact: two pairs could not return results through the real transport
  until closed.
status: FIXED — Wave 2. `_ref_evidence()` re-emits each Sentinel ref
  as a declared Evidence record (epistemic=observed, verbatim id,
  producer=api-sentinel-forge). Routed anomaly/incident/doctor-handoff
  asks now pass result-integrity verification
  (evidence/theforge/runs/*/verification.json status=passed).
```

---

### Severity roll-up

| Severity | Count | Ids |
|---|---|---|
| P0 | 0 | — |
| P1 | 11 | F003, F004, F005, F007*, F008, F009*, F010*, F011*, F012*, F015*, F018* |
| P2 | 3 | F006, F016, F017 |
| P3 | 3 | F001, F013, F014* |
| Refuted | 1 | F002 (no defect) |

*F007 and F012 FIXED in Wave 1 (status inline). F009, F010, F011, F014,
F015 and F018 FIXED in Wave 2 (status inline); F016/F017 recorded as
peer-side / wire-contract limitations with honest workarounds.
Deferred: F005 → Wave 3, F006 → Wave 4.

No P0s: nothing in the audit corrupts data, executes unsanctioned code, or
fails structurally under the supported deployment — the unavailable
integrations are honest named states, and the authz placeholder is bounded by
loopback. F007 is the closest-to-P0 item and is classified P1 per the rubric
(single-user loopback scope), with the note that `--allow-remote` exposure
escalates it.
