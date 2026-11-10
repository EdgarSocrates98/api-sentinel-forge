# Evidence verification

`lab/verify_evidence.py` now rejects absolute and traversal paths, Windows
drive paths, symlink paths, duplicate entries, invalid step state/truth,
oversized manifests/files, missing files, size/hash drift, unmanifested
steps, and receipt/manifest disagreement.

Verification is offline and runs against the bundle root, not the source
checkout. Negative tests cover traversal, symlink escape, tampering, missing
files, duplicate paths, and size/hash mismatches.
