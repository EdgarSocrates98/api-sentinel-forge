# Release E2E

The required Golden Path is S1–S18 with real local traffic, deep recovery
parity, independent bundle verification, and cleanup. A previous diagnostic
run with zero traffic is not release evidence. A later run that used a stale
pre-fix wheel is also excluded. Corrected run `20261110T230014Z-7286ba79` is
the valid Windows evidence: 14 PASS, 0 FAIL, 4 BLOCKED, 18/18 steps in the
portable bundle, and independent verification passed from the checkout and
from a copied bundle root.
