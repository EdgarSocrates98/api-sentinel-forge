# API Sentinel Forge — instructions for agents

Local-first API reliability, runtime intelligence and incident investigation.
Read `docs/02-architecture-design.md` before making structural changes and
`CONTEXT.md` before naming anything.

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

## Commands

```bash
uv sync                                # deps (creates uv.lock)
python -m pytest -q                    # tests
python -m ruff check .                 # lint, line length 100
python -m mypy src                     # strict typing
python -m build                        # wheel + sdist
api-sentinel doctor                    # environment health after install
```

## Layout

- `domain/` — entities, identity, quality state; pure, injectable.
- `application/` — use cases + ports (repository/store protocols).
- `infrastructure/` — SQLite storage, migrations, redaction, logging.
- `contracts/v1/` — typed models matching `contracts/v1/*.json`.
- `integrations/` — anti-corruption adapters per Forge (Cycle 5+).
- `cli/` — governed entrypoints; `api/` — local HTTP surface (Cycle 6).

## Boundaries that are load-bearing

- `detection` never knows about AWS; `incidents` never knows about Angular;
  `inventory` never depends on Code Atlas internals.
- One writer per table; cross-context references by id only.
- Missing telemetry source ≠ healthy API. Missing traffic ≠ outage.
  Missing collector ≠ unavailable service. These three confusions are bugs.

## Spec workflow

- Working specs live in `.kiro/specs/` (local, uncommitted); the committed
  queue for upcoming cycles lives in `factory/specs/inbox/`.
- Review before archiving a spec; run records under `factory/runs/`.

## Language

Think in English, answer in the user's language; committed docs are English
first, with PT-BR mirrors only where the ecosystem already has them.
