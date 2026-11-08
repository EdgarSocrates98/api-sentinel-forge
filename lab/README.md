# Lab — API Sentinel Forge

Reproducible laboratory scenarios for detection, diagnosis and E2E proof.
The 16 scenarios and their oracles are defined in `docs/13-e2e-test-strategy.md`.

## Cycle 1 scope

The Cycle-1 gate is exercised by `tests/integration/test_restart_recovery.py`:
state created in one "process" must be fully recovered by another over the
same workspace. Fixtures under `fixtures/` seed later cycles (Cycle 2+
ingestion, Cycle 4 detection).

- `fixtures/lab-001-elevated-latency.jsonl` — bounded JSONL observations for
  `payments-api` with a latency step at `2026-01-01T00:05:00Z` (baseline
  ~120 ms p95 → ~950 ms p95). Consumed by the Cycle-2 ingestion work and the
  LAB-001 detection oracle.
