# Cycle 4 — Report: Detection & Incident Lifecycle

## Goal

Build the `detection` and `incidents` bounded contexts on top of Cycle-3
aggregates and SLO assessments: a closed, versioned, deterministic detector
registry (six Level-1 rules), explainable anomalies, cooldown/hysteresis
controls, incident deduplication, and the incident state machine with an
append-only journal — governed by `docs/08-slo-and-detection-design.md`,
`docs/09-incident-lifecycle.md`, ADR-007 (deterministic detector) and
ADR-008 (current state + journal, not event sourcing).

## Acceptance criteria

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Detector registry holds versioned detectors implementing the protocol (`detector_id`, `detector_version`, `evaluate(request, context) → DetectionResult`) with typed results carrying explicit evidence and limitations | PASS | `detection/protocol.py` (`Detector` runtime-checkable Protocol, `DetectionRequest`/`DetectionContext`/`DetectionResult`, `AnomalyDraft` with `evidence_refs`/`unknowns`); `registry.py` closed set; `test_builtin_detectors_satisfy_protocol`, `test_registry_kinds_is_the_closed_set`, `test_registry_unknown_kind_is_a_named_error` |
| 2 | Level-1 threshold rules: error rate over limit, p95 over latency objective, source stopped sending, accelerated error-budget consumption, timeout growth after a deploy, unexpected status on an operation | PASS | `detection/rules.py`: `ErrorRatioOverLimit`, `P95OverLatencyTarget`, `SourceSilence`, `BudgetBurnAcceleration`, `TimeoutGrowthAfterDeploy`, `StatusUnexpectedOnOperation`; per-rule unit tests (`test_error_ratio_*`, `test_p95_*`, `test_source_*`, `test_budget_burn_*`, `test_timeout_growth_*`, `test_status_unexpected_*`) |
| 3 | Simple baselines (median/MAD, EWMA, window comparison) available as detector inputs; each evaluated on a known corpus, never claims root cause | PASS | `detection/baselines.py` (`median`, `median_abs_dev`, `ewma`, `window_ratio`, `summarize_baselines`); `test_median_mad_ewma_window_ratio_oracles`, `test_summarize_baselines_on_known_corpus`; deploy rule emits `temporal_proximity_is_not_causation` |
| 4 | Per-rule cooldown plus hysteresis against flapping; alert frequency limited and planned suppression supported | PASS | `RulePolicy` parses `min_interval_seconds`/`confirm_windows`/`suppressed_until`/`suppress_reason`; `evaluate.py` enforces them; `test_hysteresis_requires_breach_in_every_confirm_window`, `test_cooldown_suppresses_flapping`, `test_planned_suppression_is_recorded_not_silent` |
| 5 | Deduplication groups anomalies into one incident; originals preserved with recorded justification | PASS | `group_key_for(service, operation_scope, detector_id)`; `attach_anomaly` journals an `anomaly_attached` event and the incident's `dedup.grouped_by` records the justification; `test_attach_second_anomaly_journals_the_grouping`, `test_lab002_replay_never_duplicates` — see *Deviations* for grouping dimensions |
| 6 | Incident lifecycle states; operational status independent of confidence classification | PASS | `incidents/models.py` `IncidentStatus` (9 states) vs `Confidence` (`OBSERVED/CORRELATED/HYPOTHESIZED/VERIFIED`); `test_hypothesis_confidence_is_independent_of_status`, `test_verified_hypothesis_requires_independent_evidence` |
| 7 | Severity `SEV1..SEV4 + INFORMATIONAL` configured per service/rule — never from HTTP status alone | PASS | severity lives in `DetectionRule.config["severity"]` (defaults: degradation SEV3, unexpected status SEV4, source silence INFORMATIONAL); rules read status codes only as *signals*; `test_worse_configured_severity_escalates_with_rule_revision` |
| 8 | Every state change appends an `incident_event`; first_observed/detected/acknowledged/resolved timestamps tracked | PASS | `incidents/journal.py` + `incident_events` table; `Incident.first_observed_at/detected_at/acknowledged_at/resolved_at`; `test_ack_then_resolve_sets_timestamps`, journal verified in CLI smoke |
| 9 | Reopen only via journaled event; a closed incident is never edited without history | PASS | `RESOLVED/DISMISSED → REOPENED` journaled; `CLOSED` terminal — attaching a new anomaly to it is a named error, but journaled notes still append; `test_resolved_reopens_only_via_journal`, `test_dismissed_can_reopen_but_closed_is_terminal`, `test_closed_incident_still_takes_journaled_notes`, `test_attach_to_closed_status_is_a_named_error` |
| 10 | Escalation defined by versioned rules, recorded with the rule revision that fired it | PASS | every persisted anomaly carries `RuleRef(rule_id, rule_revision, detector_id, detector_version)`; a later rule revision with a more severe configured severity escalates the incident and the event records `rule_revision`; `test_worse_configured_severity_escalates_with_rule_revision`, `test_result_carries_rule_identity_and_explanation` |
| 11 | Incident persistence + audit: current-state tables + journal; `revision` concurrency; audit events append-only | PASS | `migrations/0004_incidents.sql` (`detection_rules`, `anomalies`, `incidents`, `incident_events`); `TransitionIncident` requires `expected_revision`; `IncidentEventRepository` exposes no update/delete; deterministic `event_id` makes replay idempotent; `test_incident_events_repo_has_no_update_or_delete_path`, `test_incident_revision_conflict_is_a_named_error` |
| 12 | LAB-001 per-operation elevated latency; LAB-002 persistent 503 → error-ratio breach + incident + detected recovery; zero duplicate incidents under replay | PASS | `test_lab001_elevated_latency_fires_p95`, `test_lab002_persistent_503_fires_error_ratio_once`, `test_lab002_clean_window_journals_recovery_observation`, `test_lab002_replay_never_duplicates`; fixture `lab/fixtures/lab-002-persistent-503.jsonl`; CLI smoke below |

## What landed

| Deliverable | Location |
| --- | --- |
| Detector protocol (`Detector`, `DetectionRequest`, `DetectionContext`, `DetectionResult`, `AnomalyDraft`, `RuleRef`, `anomaly_id_for`) — pure, no I/O, injected `now` | `src/api_sentinel/detection/protocol.py` |
| Six Level-1 detectors with math explanations, evidence refs, unknowns/limitations | `src/api_sentinel/detection/rules.py` |
| Baseline inputs: median/MAD/EWMA/window-ratio + `summarize_baselines` | `src/api_sentinel/detection/baselines.py` |
| Closed registry, immutable `DetectionRule`, `RulePolicy` (cooldown/hysteresis/suppression), `default_rules` | `src/api_sentinel/detection/registry.py` |
| `evaluate_rule` orchestration: disabled/suppressed/cooldown/confirm-windows/replay-dedup | `src/api_sentinel/detection/evaluate.py` |
| Incident + event models, `IncidentStatus`/`Confidence`/`Severity` ladders, `group_key_for`, `incident_id_for` | `src/api_sentinel/incidents/models.py` |
| Pure lifecycle functions (`open_incident`, `attach_anomaly`, `transition`, `recovery_observed`, hypotheses/actions/notes/owner) | `src/api_sentinel/incidents/lifecycle.py` |
| Journal event construction (deterministic ids, redacted payloads) | `src/api_sentinel/incidents/journal.py` |
| `detection_rules`, `anomalies`, `incidents`, `incident_events` tables | `migrations/0004_incidents.sql` |
| `DetectionRuleRepository`, `AnomalyRepository`, `IncidentRepository`, `IncidentEventRepository` (no update/delete on events) + `ReleaseRepository.list` | `application/ports.py`, `infrastructure/storage/repositories.py` |
| `RunDetection` (seed defaults → evaluate → persist anomalies → attach/open incidents → journal recovery), `ListIncidents`, `ShowIncident`, `TransitionIncident` | `application/use_cases.py` |
| `detect`, `incidents`, `incident show\|ack\|resolve\|reopen` commands | `cli/__init__.py` |
| `api.anomaly-detection`, `api.incident-investigation` → **available** | `capabilities.py` |
| LAB-002 fixture (persistent 503, 16 records) | `lab/fixtures/lab-002-persistent-503.jsonl` |

## Semantics honored

- **Detector purity**: `evaluate(request, context)` receives a frozen
  `DetectionRequest` with injected `now`; context carries only typed slices
  (observations, SLO assessments, sources, releases, recent anomalies,
  baselines) assembled by `RunDetection` through repository ports. No
  network, wall-clock, persistence or credential access is reachable from
  detectors; `test_detectors_are_deterministic_and_stateless` calls
  `evaluate` twice on identical inputs and diffs the serialized results;
  `test_detected_at_tracks_only_the_injected_now` proves only `request.now`
  is read.
- **Explainability**: every `AnomalyDraft` carries
  `explanation = {summary, math}` with the literal numbers (e.g.
  `error_ratio = (errors+timeouts)/eligible = (573+0)/999 = 0.5736 > 0.05`),
  `evidence_refs` (observation/source/release ids), `unknowns`, plus the
  rule/detector identity and revision on persist.
- **Silence ≠ outage**: `source_silence` detects a registered source that
  stopped sending (INFORMATIONAL default) and records
  `service_state_unmeasurable_while_silent` — never proof the service is
  down. A never-observed source and a no-sources service are unknowns, not
  anomalies.
- **Inert by default**: `status_unexpected_on_operation` only flags
  codes/classes the config declares; empty config →
  `no_unexpected_statuses_configured` unknown, zero anomalies.
- **Deploy ≠ cause**: `timeout_growth_after_deploy` requires a release
  inside `deploy_lookback_seconds` and attaches it as *evidence*
  (`rel:<id>@<deployed_at>`) with the explicit limitation
  `temporal_proximity_is_not_causation`.
- **Dedup + grouping audit**: anomalies sharing
  `service|operation_scope|detector_signature` attach to the unresolved
  incident; each attach journals an `anomaly_attached` event (severity
  upgrades journal `severity_escalated`) and the incident's
  `dedup.grouped_by` records the justification. Original anomalies are
  preserved verbatim in `anomalies`.
- **Severity is policy**: severity comes from rule config (business impact),
  never inferred from status-code counting; worst configured severity wins
  on escalation with the firing rule revision recorded.
- **Confidence ≠ workflow**: anomaly/hypothesis confidence
  (`OBSERVED → CORRELATED → HYPOTHESIZED → VERIFIED`) is orthogonal to
  incident status; `VERIFIED` requires independent evidence refs.
- **Journal-only history**: `incident_events` has no update/delete path;
  event ids are deterministic hashes so replay is a no-op. `revision`
  enforces optimistic concurrency — stale writers get
  `sentinel.conflict` named errors.
- **Replay-safe**: `request_id`, `anomaly_id`, `incident_id`, `event_id`
  are all deterministic; re-running detection over the same window yields
  identical ids, zero new anomalies, zero new incidents.

## Evidence

```
$ .venv/Scripts/python.exe -m pytest -q
185 passed in 4.76s

$ .venv/Scripts/python.exe -m ruff check .
All checks passed!

$ .venv/Scripts/python.exe -m mypy src
Success: no issues found in 54 source files
```

CLI smoke (fresh workspace; fixture ids remapped to generated ones):

```
$ api-sentinel ingest --source-id src_… --file lab-002-remapped.jsonl --json
{"batch_id": "batch_…", "accepted": 16, "quarantined": 0, "skipped_duplicates": 0}

$ api-sentinel detect --service-id svc_… --window-start 2026-01-01T00:00:00Z \
    --window-end 2026-01-01T00:20:00Z --now 2026-01-01T00:20:00Z --json
request_id: det_b520b1ca607e12b6bdbdbd42        (replay → identical id)
rule_error_ratio_over_limit_…   fired    error_ratio = (573+0)/999 = 0.5736 > 0.05   SEV3
rule_source_silence_…           fired    last_window_end …; now-last > 300s          INFORMATIONAL
rule_budget_burn_acceleration_… clean    unknowns: ["no_slo_assessments_in_window"]
rule_status_unexpected_…        clean    unknowns: ["no_unexpected_statuses_configured"]

$ api-sentinel detect … (identical replay)
new anomalies: 0   deduplicated: [anm_405ec…, anm_84b84…]   incidents_created: []

$ api-sentinel incidents --status open --json    (LAB-002 + LAB-001, 4 incidents)
inc_431e… REOPENED   SEV3          svc_…|service|error_ratio_over_limit
inc_fef0… DETECTED   INFORMATIONAL svc_…|service|source_silence
inc_4ea0… DETECTED   SEV3          svc_…|service|p95_over_latency_target   (/payments/{payment_id}, p95 = 1000ms > 500ms)
inc_b10d… DETECTED   INFORMATIONAL svc_…|service|source_silence

$ api-sentinel incident ack|resolve|reopen inc_431e… --json
ack → TRIAGED (rev 2) · resolve → RESOLVED (rev 3, resolved_at set) · reopen → REOPENED (rev 4)
journal: opened → status_transition×3; a duplicate TRIAGED→TRIAGED is refused
(error[sentinel.validation]: invalid incident transition).
```

Bug found by the smoke and fixed in this cycle: `default_rules` originally
emitted service-agnostic `rule_<kind>` ids, so seeding defaults for a
*second* service violated the `(rule_id, revision)` primary key. Default
rule ids now embed `service_id` (`rule_<kind>_<service_id>`); regression
test `test_default_rules_seed_per_service_without_collision`.

## Deviations / open items

- **Dedup grouping dimensions**: `group_key` covers service + operation
  scope + detector signature, and `grouped_by` additionally records the
  shared environment (a single-workspace constant today). A release is
  carried as `evidence_refs` on the anomaly, not a grouping dimension, and
  shared-dependency correlation is deferred to the Cycle-5 cross-forge
  work (`api.deployment-correlation` stays `declared`). The spec's
  "possibly-shared dependency" is therefore partially honored.
- **Escalation**: severity escalation on re-evaluation is implemented and
  journaled with the firing rule revision; a dedicated notification/
  paging policy engine is explicitly out of scope per the spec's grill
  gate.
- **`budget_burn_acceleration`** reports `no_slo_assessments_in_window`
  when the SLO plane is empty — the lab fixtures carry no SLO definitions,
  so the rule is exercised by unit tests rather than the smoke.
- **`incidents --service-id` filter and `incident show` anomalies** are
  exercised; richer journal browsing (per-event paging) is a portal-scope
  follow-up.
