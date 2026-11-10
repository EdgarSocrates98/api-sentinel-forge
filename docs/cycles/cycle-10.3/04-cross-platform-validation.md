# Cross-platform validation

Windows local core: PASS for the corrected isolated wheel on Python 3.12
(runner boot venv) and Python 3.14.6 (quality environment). Golden Path run
`20261110T230014Z-7286ba79` produced 14 PASS and 4 honest BLOCKED states,
with zero FAIL states.

Linux hosted core CI: PASS in run `42922701461` on `ubuntu-latest / py3.12`.
The fixed-revision federated Golden Path also ran on Linux in run
`42922701484`, job `130201255367`: 14 PASS, 0 FAIL, and 4 explicit BLOCKED
states. Its bundle verification passed and its artifact was uploaded.

This proves the Linux local/federated profile, but does not prove the blocked
Atlas, browser, Bridge-provider, or Lens-provider resources.

The runner no longer assumes `.venv/Scripts/python.exe`; paths with spaces
and Unicode are exercised by regression tests.
