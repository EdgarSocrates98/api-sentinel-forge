# Federated CI

The required CI design is a core cross-platform matrix plus a manually
authorized federated-local job using fixed peer revisions. Provider-backed
Bridge/Lens and AWS lanes remain explicit optional states; they must never be
reported as PASS when credentials/providers are absent.

The tested product head is `5df1c6e2f90d6ff9d7da2e5be6594a0d220f383d`.
Core CI run `42934058383` passed on both `ubuntu-latest / py3.12` and
`windows-latest / py3.12`, including Ruff, Mypy, the 634-test offline suite,
wheel/sdist build, and post-install doctor smoke.

The dedicated fixed-revision federated workflow run `42934058334` also passed
on Linux. It checked out and asserted the peer SHAs, ran the real Golden Path,
verified the produced bundle independently, and uploaded artifact
`cycle-10.3-federated-evidence` (`13816749115`). The downloaded bundle run was
`20261111T004728Z-e2be3cce`. The Golden Path result was
14 PASS, 0 FAIL, and 4 honest BLOCKED states: S10 Atlas database, S12 Bridge
provider, S13 Lens provider, and S14 Playwright runtime. These limitations are
not converted into PASS claims.
