# theforge-apisentinel — forge/v1 adapter for API Sentinel

A hand-built adapter distribution (Cycle 5), following the `theforge_doctorapi`
conventions. **Shipped here for review** and copied into
`the-forge/adapters/apisentinel` at registration — the same lifecycle as
`forge-knowledge/api-sentinel-forge.json`. It never modifies the-forge and
never imports `theforge`; it imports `api_sentinel`'s public boundary only.

## What it does

- `catalog.py` — `CapabilitySpec`s mapping Sentinel public seams to
  `api.runtime-observation`, `api.slo-assessment`, `api.anomaly-detection`,
  `api.incident-investigation`, `api.deployment-correlation`.
- `record.py` — rewrites `native_surface.json` (which public seams exist) and
  drives the **live catalog verification**: it runs the real
  `SentinelBoundary.describe()`/`capabilities()` and records which of the five
  declared ids come back `supported` into `catalog_verification.json`. A
  semantic collision surfaces as a missing/non-supported id — never a
  workaround.
- `__main__.py` — the forge/v1 stdio bridge: Request on stdin →
  `SentinelBoundary.handle()` → Response on stdout.

## Re-record the surface + verification

```bash
python -m theforge_apisentinel.record    # in the specialist's interpreter
```

This regenerates `native_surface.json` and `catalog_verification.json` as run
evidence — the acceptance criterion's "verification of those ids against the
live capability catalog recorded in the run evidence".
