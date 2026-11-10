# Portability design

The lab resolves Python and installed entry points through `lab/runtime.py`.
It validates candidates by executing a bounded version probe, supports
`Scripts/` and `bin/`, preserves argv-list invocation, and creates a unique
run directory. `--skip-traffic-sleep` remains diagnostic-only; release
evidence must use real closed windows.

Remaining validation: hosted Linux and a clean checkout without `.git`.
