# Findings Ledger

| ID | Status | Evidence | Resolution |
|---|---|---|---|
| C102-F001 | FIXED | S16 failed with `restore integrity=None` and parity `2==1`, `8==1`, `2==1` after a prior run | Unique restore workspace per run; S16 PASS |
| C102-F002 | FIXED/IMPROVED | Runner used persistent workspace, fixed ports and host-specific tool paths | Run isolation, configurable paths and dynamic ports |
| C102-F003 | FIXED | `RichLog(markup=True)` received raw CLI output | External output is written as literal `rich.text.Text` |
| C102-F004 | PASS | Doctor timeout/output/cleanup tests and worker boundary | Existing bounded worker retained; negative suite PASS |
| C102-F005 | FIXED/HARDENED | HTTP adapter accepted default `fake` and unbounded responses | Explicit provider, loopback validation, no redirects/proxy, bounded JSON |
| C102-F006 | PASS | Headless TUI suite | Safety regression and existing navigation coverage |
| C102-F007 | BLOCKED/DEFINED | No external Lens provider on host | Fixture/imported/real states remain distinct |
| C102-F008 | IN PROGRESS | Cycle documentation was stale | This directory and evidence manifest |
| C102-F009 | PASS | Source quality/unknown state tests | Existing provenance and limitation states preserved |
| C102-F010 | PASS | CLI/install/doctor tests | Existing guided install and named diagnostics retained |
| C102-F011 | PASS LOCAL | S1–S18 rerun | S12/S13 external blocks remain explicit |
| C102-F012 | PASS | Incident/investigation and portal E2E | Existing real integration preserved |
| C102-F013 | FIXED | Persistent lab artifacts contaminated restore | Per-run temporary restore destination |
| C102-F014 | PASS | TUI command catalog and security tests | No shell intermediary; literal output and confirmations retained |
