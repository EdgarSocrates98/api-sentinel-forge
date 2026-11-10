# Federated CI

The required CI design is a core cross-platform matrix plus a manually
authorized federated-local job using fixed peer revisions. Provider-backed
Bridge/Lens and AWS lanes remain explicit optional states; they must never be
reported as PASS when credentials/providers are absent.

Current repository CI baseline was PASS on the recorded main commit. The
Cycle 10.3 workflow change is pending final local validation and hosted run.
