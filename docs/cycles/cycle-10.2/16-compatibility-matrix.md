# Compatibility Matrix

| Surface | Result |
|---|---|
| Python 3.11+ | Source policy retained; host run on 3.14 |
| Windows | Local E2E PASS |
| Linux | CI verification required before cross-OS claim |
| CLI/API/contracts | Existing suite PASS |
| SQLite/migrations | Recovery and packaging tests PASS |
| TUI optional extra | Headless tests PASS |
| Bridge | Local contract PASS; external provider blocked |
| Lens | Reference contract preserved; provider blocked |
| Portal | Browser flow PASS |
| AWS | Opt-in only; not invoked |
