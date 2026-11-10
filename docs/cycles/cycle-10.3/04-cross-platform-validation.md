# Cross-platform validation

Windows local core: PASS for the corrected isolated wheel on Python 3.12
(runner boot venv) and Python 3.14.6 (quality environment). Golden Path run
`20261110T230014Z-7286ba79` produced 14 PASS and 4 honest BLOCKED states,
with zero FAIL states.

Linux: NOT_RUN locally in this Windows workspace. Hosted Linux evidence must
be attached to the final CI run before this gate can be marked PASS.

The runner no longer assumes `.venv/Scripts/python.exe`; paths with spaces
and Unicode are exercised by regression tests.
