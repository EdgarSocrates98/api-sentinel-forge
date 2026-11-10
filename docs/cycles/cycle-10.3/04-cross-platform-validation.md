# Cross-platform validation

Windows local core: PASS for the corrected isolated wheel on Python 3.12
(runner boot venv) and Python 3.14.6 (quality environment). Golden Path run
`20261110T230014Z-7286ba79` produced 14 PASS and 4 honest BLOCKED states,
with zero FAIL states.

Linux hosted core CI: PASS in run `42921827462` on `ubuntu-latest / py3.12`.
The full federated Golden Path itself has not been run on Linux and remains
NOT_RUN; it must not be inferred from the core matrix.

The runner no longer assumes `.venv/Scripts/python.exe`; paths with spaces
and Unicode are exercised by regression tests.
