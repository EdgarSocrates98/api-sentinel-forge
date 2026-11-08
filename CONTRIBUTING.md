# Contributing

Thanks for helping build API Sentinel Forge.

## Ground rules

- Read `AGENTS.md` invariants and `CONTEXT.md` before naming anything.
- Deterministic core: no network, no wall-clock, no credentials inside
  `domain/` and `application/` — use ports and injected collaborators.
- Every bug fix starts as a failing test. Every feature starts as a spec
  (`factory/specs/` or `.kiro/specs/`).
- One problem per change; keep diffs surgical.

## Setup

```bash
uv sync                 # or: pip install -e .[dev]
python -m pytest -q
python -m ruff check .
python -m mypy src
```

## Change flow

1. Spec or failing test first.
2. Implement inside the owning bounded context — no cross-context imports.
3. Keep contracts versioned; additive optional fields keep the version,
   meaning changes need a new one.
4. Update docs that name the behavior you touched.
5. Green tests + lint + types before review.

## Versioning and releases

Pre-1.0: public contract surfaces may still evolve, but only behind explicit
versioned schemas. Releases are cut only after `docs/17-release-readiness.md`
criteria are met.
