# Release E2E

The required Golden Path is S1–S18 with real local traffic, deep recovery
parity, independent bundle verification, and cleanup. A previous diagnostic
run with zero traffic is not release evidence. A later run that used a stale
pre-fix wheel is also excluded.

Corrected Windows run `20261111T003852Z-2bba1df4` is retained as local runtime
evidence: 16 PASS, 0 FAIL, 2 BLOCKED (S12/S13). The canonical distributed
bundle is the Linux CI artifact from run `42934058334`, bundle run
`20261111T004728Z-e2be3cce`: 14 PASS, 0 FAIL, 4 BLOCKED, 18/18 steps, 28
manifest entries, and independent verification passed after download outside
the runner.
