# E2E Portability

The runner now supports `sys.executable`, `shutil.which`, explicit environment
overrides (`FORGE_PORTAL_DIR`, `FORGE_PLAYWRIGHT_PYTHON`, `THEFORGE_BIN`,
`CODE_ATLAS_DB`, `AWS_CLI`) and dynamically selected loopback ports. Optional
Portal, Atlas, Bridge, Lens and AWS capabilities remain named states when
absent; no cloud credential is requested automatically.

Each run keeps process logs and artifacts under its run root, creates isolated
workspaces, probes health before use and verifies ports after cleanup. The
Windows local run is real. A Linux run is not claimed by this evidence and
must be executed by Linux CI before a cross-OS production claim.
