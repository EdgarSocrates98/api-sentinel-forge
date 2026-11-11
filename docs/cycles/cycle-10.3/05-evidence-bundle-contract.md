# Evidence bundle contract

Cycle 10.3 bundles use `api-sentinel/EvidenceManifest/v1` plus
`api-sentinel/EvidenceBundle/v1`. The portable root contains relative paths,
`bundle.json`, receipt, manifest, steps, artifacts, and runtime metadata.

The bundle is copied from the isolated run and sanitizes checkout/home paths
and the lab token before hashes are calculated. A bundle is not accepted when
any referenced file is absent.
