# Cross-platform validation

Windows local core: PASS for the corrected isolated wheel on Python 3.12
(runner boot venv) and Python 3.14.6 (quality environment). Golden Path run
`20261111T003852Z-2bba1df4` produced 16 PASS, 0 FAIL and 2 honest BLOCKED
states (Bridge/Lens providers), with real Code Atlas and Portal browser
execution.

Linux hosted core CI: PASS in run `42934058383` on `ubuntu-latest / py3.12`.
The fixed-revision federated Golden Path also ran on Linux in run
`42934058334`: 14 PASS, 0 FAIL, and 4 explicit BLOCKED states. Its bundle
verification passed; artifact `13816749115` was downloaded and independently
verified. The bundle run ID was `20261111T004728Z-e2be3cce`.

This proves the Linux local/federated profile, but does not prove the blocked
Atlas, browser, Bridge-provider, or Lens-provider resources in that hosted
environment; the Windows local profile separately proved Atlas and browser.

The runner no longer assumes `.venv/Scripts/python.exe`; paths with spaces
and Unicode are exercised by regression tests.
