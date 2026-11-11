# Unresolved risks

* Linux hosted execution passed for core and fixed-revision federated CI, but
  the local Windows workspace still cannot independently reproduce Linux.
* Code Atlas is blocked locally when its real database is unavailable.
* Portal browser evidence is blocked when the selected Python lacks Playwright.
* Bridge provider and Lens provider execution require external authorization.
* Hosted CI and PR checks are recorded against final PR HEAD
  `5df1c6e2f90d6ff9d7da2e5be6594a0d220f383d`; merge-to-main revalidation is
  still required before release closure.

These are explicit BLOCKED/NOT_RUN states, not approvals.
