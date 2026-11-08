# API Sentinel Forge — guide for agents

Local-first API reliability, runtime intelligence and incident investigation.
Same invariants as `AGENTS.md`; keep them byte-aligned when editing.

<!-- sentinel:invariants:begin -->
## Invariants

- Deterministic core: `src/api_sentinel/domain` and
  `src/api_sentinel/application` never perform network I/O, never read the
  wall clock directly (inject `clock`), never touch credentials, and never
  import sibling Forge packages. External systems sit behind
  `application/ports.py` + adapters under `integrations/`.
- Quality state is separate from operational state: a `PARTIAL`/`STALE`
  source must never produce a fabricated healthy conclusion, and an
  `UNKNOWN` contract version is rejected explicitly — never silently
  defaulted.
- Idempotent persistence: reprocessing a batch must not duplicate points;
  counters that reset never produce negative rates; a closed `Incident`
  is never rewritten without a journaled event.
- Evidence integrity: `EvidenceArtifact` payloads are content-hashed
  (SHA-256); truncated evidence records its loss; audit events are
  append-only.
- Secrets hygiene: nothing persisted crosses `infrastructure/security/redact.py`
  without scanning; receipts and logs never contain credentials.
- Contracts are versioned (`api-sentinel/<Name>/v1`); breaking a shape means
  a new version, not an edit.
- Sibling Forges are consumed through their public contracts only — the
  Sentinel never reimplements Doctor analysis, Atlas graphs, The Forge
  routing, Bridge sessions, Lens tracing or Data Sentinel diagnostics.
<!-- sentinel:invariants:end -->

## Persistent rules

- Deterministic core; adapters own the outside world.
- Honest states over confident guesses: `UNKNOWN`/`UNVERIFIED` are valid
  answers, fabricated health is a defect.
- Every claimed completion requires fresh verification evidence.
- Version `0.1.0` — do not bump without an owner-documented rationale in
  `docs/12-installation-and-distribution.md`; do not publish.

## More context

`docs/02-architecture-design.md`, `docs/03-domain-model.md`,
`docs/07-security-threat-model.md`, `docs/adr/`, `CONTEXT.md`.
