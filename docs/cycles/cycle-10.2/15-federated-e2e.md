# Federated E2E

The final local run executes S1–S18 with real processes, real HTTP traffic,
real storage, Doctor boundary, Atlas read-only seam, The Forge route and
Portal browser flow. The expected optional-provider outcome is 16 PASS and 2
BLOCKED (S12 Bridge provider, S13 Lens provider/manifest). Receipt and hash
manifest are generated under `evidence/` and independently verified.
