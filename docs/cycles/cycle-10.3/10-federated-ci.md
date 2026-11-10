# Federated CI

The required CI design is a core cross-platform matrix plus a manually
authorized federated-local job using fixed peer revisions. Provider-backed
Bridge/Lens and AWS lanes remain explicit optional states; they must never be
reported as PASS when credentials/providers are absent.

The PR CI run `42921827462` passed on both `ubuntu-latest / py3.12` and
`windows-latest / py3.12`, including Ruff, Mypy, 634-test offline suite,
wheel/sdist build, and post-install doctor smoke. The dedicated
`federated-ci.yml` workflow is present in this branch but cannot be dispatched
until it exists on the repository's default branch; GitHub returned 404 for a
pre-merge dispatch request. This is recorded as an external limitation.
