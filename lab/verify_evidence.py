# ruff: noqa: T201 -- lab verifier: stdout IS the report.
"""Independent evidence verifier (C101-F002).

Recomputes every hash recorded in a federated-E2E manifest and
cross-checks receipt ↔ manifest ↔ step files. Run it against a finished
(or in-progress) run:

    python lab/verify_evidence.py \
        --receipt docs/cycles/cycle-10.1/evidence/federated-e2e-receipt.json \
        --manifest docs/cycles/cycle-10.1/evidence/federated-e2e-manifest.json \
        --root .

Checks — each failure is reported, never papered over:

1. Every manifest entry: file exists, sha256 matches, byte size matches.
2. Every step embedded in the receipt has a manifest entry for its
   ``steps/<id>.json`` file.
3. The receipt's embedded step records are byte-identical (as JSON) to
   the step files on disk — the receipt cannot disagree with the record.
4. Every ``steps/S*.json`` file on disk appears in the manifest — no
   unmanifested evidence.
5. The receipt summary counters equal the actual step statuses.

Exit 0 only when every check holds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="repo root the manifest's relative paths resolve under",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    failures: list[str] = []

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FATAL manifest unreadable: {exc}")
        return 2
    try:
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FATAL receipt unreadable: {exc}")
        return 2

    if manifest.get("schema") != "api-sentinel/EvidenceManifest/v1":
        failures.append(
            f"manifest schema {manifest.get('schema')!r} is not "
            "api-sentinel/EvidenceManifest/v1"
        )
    entries = manifest.get("files") or []
    if not isinstance(entries, list):
        failures.append("manifest 'files' is not a list")
        entries = []

    # 1 — every manifest entry verifies byte-for-byte
    manifest_paths: set[str] = set()  # forward-slash normalized
    for entry in entries:
        rel = str(entry.get("path", "")).replace("\\", "/")
        manifest_paths.add(rel)
        target = (root / rel).resolve()
        if not str(target).startswith(str(root)):
            failures.append(f"{rel}: escapes the declared root")
            continue
        if not target.is_file():
            failures.append(f"{rel}: missing on disk")
            continue
        digest = _sha256(target)
        if digest != entry.get("sha256"):
            failures.append(f"{rel}: sha256 mismatch "
                            f"({entry.get('sha256')} != {digest})")
        size = target.stat().st_size
        if size != entry.get("bytes"):
            failures.append(f"{rel}: byte size mismatch "
                            f"({entry.get('bytes')} != {size})")

    steps = receipt.get("steps") or []
    if not isinstance(steps, list):
        failures.append("receipt 'steps' is not a list")
        steps = []
    step_ids = [str(s.get("id")) for s in steps if isinstance(s, dict)]

    # 2 — every embedded step has a manifest entry
    for sid in step_ids:
        rel = next(
            (p for p in manifest_paths if p.endswith(f"steps/{sid}.json")),
            None,
        )
        if rel is None:
            failures.append(f"step {sid}: no steps/{sid}.json manifest entry")

    # 3 — embedded records are identical to their step files
    for s in steps:
        if not isinstance(s, dict) or "id" not in s:
            continue
        sid = str(s["id"])
        rel = next(
            (p for p in manifest_paths if p.endswith(f"steps/{sid}.json")),
            None,
        )
        if rel is None:
            continue
        target = (root / rel).resolve()
        try:
            on_disk = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            failures.append(f"steps/{sid}.json unreadable: {exc}")
            continue
        if on_disk != s:
            failures.append(f"step {sid}: receipt record != step file")

    # 4 — no step file on disk goes unmanifested
    steps_dirs = {
        (root / rel).parent for rel in manifest_paths if "steps/" in rel
    }
    for d in steps_dirs:
        for f in d.glob("S*.json"):
            rel_f = str(f.resolve().relative_to(root)).replace("\\", "/")
            if rel_f not in manifest_paths:
                failures.append(f"{rel_f}: step file not in manifest")

    # 5 — summary counters match the actual steps
    summary = receipt.get("summary") or {}
    actual = {
        "total": len(steps),
        "passed": sum(1 for s in steps if s.get("status") == "pass"),
        "failed": sum(1 for s in steps if s.get("status") == "fail"),
        "blocked": sum(1 for s in steps if s.get("status") == "blocked"),
        "not_run": sum(1 for s in steps if s.get("status") == "not_run"),
    }
    for key, want in actual.items():
        if summary.get(key) != want:
            failures.append(
                f"summary.{key}: declared {summary.get(key)} != actual {want}"
            )

    verified = len(entries) - len([f for f in failures if f])
    print(f"manifest entries: {len(entries)}; steps: {len(steps)}")
    if failures:
        for f in failures:
            print(f"FAIL {f}")
        print(f"verification: FAILED ({len(failures)} discrepancies)")
        return 1
    print(f"verification: OK ({verified} entries, {len(steps)} steps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
