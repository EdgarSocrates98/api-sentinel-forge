# Cycle 10.3 — Baseline

## Repository heads

| Repository | Branch | HEAD | Current CI evidence |
|---|---|---|---|
| API Sentinel Forge | `main` | `643f394ae4d3e2246092afc8ccb1a3482354762d` | CI run `42812456710`, PASS |
| The Forge | `main` | `4f0726f742822224c2fafa0ee9f7aa36b2a778f6` | CI run `42903893124`, PASS |
| Forge Portal | `main` | `63518efd8fdf1ee458e346fcdcb3e61bbcde8b6a` | CI run `42890978157`, PASS |
| Forge Agent Bridge | `main` | `995e59ea9bbaeee3e9a3c920d94294f2f7ddb9ba` | CI run `42839348003`, PASS |
| Forge Doctor API | `main` | `3a5a9a860318cb277292a23007879a1e2bf45b9b` | quality run `42850804524`, PASS |
| Code Atlas Forge | `main` | `c164ea5e2232d2660ad4c9bcf60f63469b604c2c` | CI run `42851164026`, PASS |
| Forge Lens | `main` | `cd9bba04bf19568b7a9116fea99ca09145c24b53` | CI run `42845275747`, PASS |

All seven remote repositories had no open PRs at audit time. The API Sentinel
working tree was clean; other local peer checkouts were intentionally not
used because they contained unrelated local changes.

## Local baseline

The exact local results are recorded in `evidence/baseline.json`:

- Windows 11 / Python 3.14.6.
- `uv sync --locked --all-extras` completed.
- `uv run pytest -q`: 627 passed, 8 skipped.
- `uv run ruff check .`: PASS.
- `uv run mypy src`: PASS, 121 source files.
- `uv build`: wheel and sdist PASS, version 0.1.0.
- Real federated E2E: 11 PASS, 5 FAIL, 2 BLOCKED.

The baseline is not a release decision. Linux remains unexecuted locally and
must be proven by hosted CI or an authorized Linux environment.

## Final remote revalidation

The remote state was re-queried before final acceptance. Six repositories
remain at the historical heads above. `forge-agent-bridge` advanced only with
the documentation/logo commits `291ac31e` and `e4748b66`; the comparison
contains no runtime or contract changes. Cycle 10.3 federated CI therefore
keeps the fixed tested peer `995e59e`, while recording the current remote main
explicitly:

| Repository | Current remote main | Latest relevant CI |
|---|---|---|
| Forge Agent Bridge | `e4748b66b385aa0f2a7b55913f59efcc93719c5a` | CI `42930318793`, PASS |

This is a `FIXED_ALREADY` documentation-only drift, not a reason to open a
peer PR or change the federated contract pin.
