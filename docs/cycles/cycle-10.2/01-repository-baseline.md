# Repository Baseline

| Field | Result |
|---|---|
| Repository | `api-sentinel-forge` |
| Branch at baseline | `main` |
| Commit | `f8996d8fa9b78da8acd4260a36901133e8f1060c` |
| Host | Windows, America/Sao_Paulo |
| Python | 3.14.6 (project floor: 3.11) |
| uv | 0.12.23 |
| Tests | 626 passed, 3 honest skips |
| Ruff | PASS |
| mypy | PASS |
| wheel/sdist | PASS (`0.1.0`) |
| Federated E2E | 16 PASS, 2 BLOCKED external |

The baseline suite was run from the clean `main` checkout before the Cycle
10.2 patch. The three skips are the existing install-provisioning and
notice-only CLI cases; none hides a failed requirement.
