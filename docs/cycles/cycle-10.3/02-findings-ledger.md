# Cycle 10.3 — Findings Ledger

| ID | Severity | Owner | Status at baseline | Evidence / next action |
|---|---|---|---|---|
| C103-F001 | P1 | `lab/federated_e2e.py` | FIXED_LOCAL | `lab/runtime.py` validates `Scripts/`/`bin/` Python and entrypoints; corrected wheel run passed S1. |
| C103-F002 | P1 | E2E/evidence | FIXED_LOCAL | Cycle 10.3 bundle stores relative files under `docs/.../bundles/<run_id>`. |
| C103-F003 | P1 | `lab/verify_evidence.py` | FIXED_LOCAL | Canonical containment, symlink rejection, duplicate/path validation and size limits. |
| C103-F004 | P1 | E2E/recovery | FIXED_LOCAL | S16 compares schema, complete rows, SQLite checks and blob hashes. |
| C103-F005 | P2 | TUI | FIXED_LOCAL | Worker RichLog writes are scheduled through Textual's app thread. |
| C103-F006 | P2 | TUI/catalog | FIXED_LOCAL | Execution/display argv are separated; sensitive flags are masked. |
| C103-F007 | P2 | Bridge | FIXED_LOCAL | Error body reads are capped at 1 MiB and redacted. |
| C103-F008 | P1 | CI | FIXED_PENDING_HOSTED | `federated-ci.yml` added with fixed peer SHAs; hosted dispatch pending. |
| C103-F009 | P1 | Release/evidence | FIXED_LOCAL | Bundle independently verified from checkout and copied root. |
| C103-F010 | P2 | External integrations | CONFIRMED_EXTERNAL | S12/S13 are honestly BLOCKED without provider credentials/manifests; keep optional and explicit. |
| C103-F011 | P2 | Runner UX | FIXED_LOCAL | stdio is configured before argparse; Windows `--help` exits 0. |
| C103-F012 | P2 | Runner contract | FIXED_LOCAL | Diagnostic option is parsed and labeled non-release. |
| C103-F013 | P1 | Federated E2E | FIXED_LOCAL | Corrected 66-second run passed all mandatory local steps with no FAIL. |

No finding is marked fixed from documentation alone. A status can move to
`FIXED` only after a targeted test and a real evidence artifact pass.
