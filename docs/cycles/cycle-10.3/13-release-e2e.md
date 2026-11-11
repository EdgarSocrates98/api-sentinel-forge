# Release E2E

The required Golden Path is S1–S18 with real local traffic, deep recovery
parity, independent bundle verification, and cleanup. A previous diagnostic
run with zero traffic is not release evidence. A later run that used a stale
pre-fix wheel is also excluded.

Corrected Windows run `20261110T230014Z-7286ba79` is retained as local runtime
evidence: 14 PASS, 0 FAIL, 4 BLOCKED. The canonical distributed bundle is the
Linux CI artifact from run `42927110685`, bundle run
`20261110T235432Z-e24b641b`: 14 PASS, 0 FAIL, 4 BLOCKED, 18/18 steps, 28
manifest entries, and independent verification passed after download outside
the runner.
