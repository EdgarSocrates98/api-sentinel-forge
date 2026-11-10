# Release Readiness

| Gate | State | Evidence |
|---|---|---|
| A Recovery | PASS | S16 + recovery tests |
| B E2E portability | PASS local / NOT_RUN Linux | runner + CI requirement |
| C TUI | PASS | TUI suite + literal output regression |
| D Doctor | PASS | Doctor process/negative tests |
| E Bridge/Lens | PASS with external limitations | S12/S13 named BLOCKED |
| F Portal | PASS local | S14 4/4 browser steps |
| G Release | CONDITIONAL GO | wheel, sdist, tests, receipt |

Decision: `CONDITIONAL GO` for local-first release evidence. A production or
cross-OS claim requires hosted CI on the candidate commit and optional Bridge
and Lens providers where those integrations are part of the deployment.
