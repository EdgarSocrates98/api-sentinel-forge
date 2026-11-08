# Quality Gate — review checklist before archiving a spec

A spec in `factory/specs/active/` may move to `archive/` only after every
applicable item below is checked, with evidence captured under
`factory/runs/`. Mark items N/A explicitly when they do not apply —
never skip silently.

## 1. Acceptance criteria coverage

- [ ] Every `- [ ]` acceptance criterion in the spec is either verified
  with evidence (test, command output, lab record) or explicitly waived
  by the owner in the run notes.
- [ ] Each criterion maps to blueprint/docs content — no invented
  features were implemented beyond the spec.

## 2. Boundary respect (docs/02-architecture-design.md)

- [ ] Bounded contexts stayed exclusive: `detection` never knows AWS,
  `incidents` never knows the Portal/frontend, `inventory` never depends
  on Code Atlas internals.
- [ ] One writer per table; cross-context references by id only.
- [ ] Domain/application layers stayed deterministic: no network I/O, no
  wall clock (injected `clock`), no credentials, no sibling Forge
  imports.
- [ ] External systems sit behind ports + adapters under
  `integrations/`.

## 3. Verification evidence

- [ ] Every command in the spec's `verification:` frontmatter ran with
  output captured (e.g., `python -m pytest -q`, `python -m ruff check .`,
  `python -m mypy src`, `python -m build` where listed).
- [ ] Spec-specific lab oracles named in the spec (LAB-xxx) have run
  records under `factory/runs/`.
- [ ] No placeholder markers (`TODO`, `FIXME`, `XXX`, `TBD`, stub
  returns) in shipped code paths.
- [ ] No secrets: known-secret scan clean; receipts, logs and persisted
  payloads contain no credentials.

## 4. Honest states

- [ ] No fabricated health: missing source/traffic/collector never
  produced a healthy or unavailable conclusion by itself.
- [ ] `UNKNOWN` states are handled explicitly — never silently
  defaulted (unknown contract versions rejected, unmapped identities
  stay `UNKNOWN`).
- [ ] `PARTIAL`/`STALE`/`SAMPLED`/`INVALID` quality propagates to every
  consumer and display.
- [ ] Sampled data was never used to infer global rates; truncated
  evidence records its loss.
- [ ] Degraded integrations produce named states, never simulated
  success.

## 5. Contracts and history

- [ ] Public envelopes remain versioned (`api-sentinel/<Name>/v1`);
  breaking changes got a new version, not an edit.
- [ ] Idempotent reprocessing produced zero duplicates on fixtures.
- [ ] Reset counters produced no negative rates; percentiles were never
  averaged.
- [ ] A closed incident was never modified without a journaled event;
  rule/SLO changes created revisions instead of rewriting history.
- [ ] Every external action passed an authorized scope and approval
  where required.

## 6. Tests prove behavior

- [ ] Tests assert behavior and oracles — they do not mirror the
  implementation.
- [ ] Negative cases and invariants (§21.1) relevant to the cycle are
  exercised, not just happy paths.
- [ ] New code is covered by deterministic tests; flaky/time-dependent
  tests use injected clocks.

## 7. Record

- [ ] Run record written under `factory/runs/` (commands, outputs,
  oracle results, waivers).
- [ ] Reviewer sign-off recorded (independent reviewer or explicitly
  labeled inline review).
- [ ] Archive via `loop-factory archive <id> --accepted` or manual move
  + note in the run record.
