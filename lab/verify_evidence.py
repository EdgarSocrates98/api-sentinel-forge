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
from pathlib import Path, PureWindowsPath

MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_RECEIPT_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
VALID_STATUSES = {"pass", "fail", "blocked", "not_run"}
VALID_TRUTHS = {
    "LOCAL_REAL",
    "INTEGRATION_REAL",
    "EXTERNAL_REAL",
    "FIXTURE",
    "SIMULATED",
    "NOT_RUN",
    "BLOCKED",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _read_json_limited(path: Path, limit: int) -> object:
    if not path.is_file():
        raise OSError(f"not a regular file: {path}")
    if path.stat().st_size > limit:
        raise ValueError(f"file exceeds {limit} bytes: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _relative_target(root: Path, raw: object) -> tuple[str, Path] | tuple[None, None]:
    rel = str(raw or "").replace("\\", "/")
    if not rel or "\x00" in rel:
        return None, None
    # PureWindowsPath catches drive-qualified paths even when the verifier
    # itself runs on Linux.
    if Path(rel).is_absolute() or PureWindowsPath(rel).is_absolute():
        return None, None
    parts = Path(rel).parts
    if any(part in ("", ".", "..") for part in parts):
        return None, None
    candidate = root.joinpath(*parts)
    # Reject symlinks before opening anything.  resolve() then protects
    # against a symlink in an ancestor not visible from the lexical path.
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None, None
    target = candidate.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None, None
    return "/".join(parts), target


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
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
        manifest = _read_json_limited(args.manifest, MAX_MANIFEST_BYTES)
    except (OSError, ValueError) as exc:
        print(f"FATAL manifest unreadable: {exc}")
        return 2
    try:
        receipt = _read_json_limited(args.receipt, MAX_RECEIPT_BYTES)
    except (OSError, ValueError) as exc:
        print(f"FATAL receipt unreadable: {exc}")
        return 2

    if not isinstance(manifest, dict):
        failures.append("manifest must be a JSON object")
        manifest = {}
    if manifest.get("schema") != "api-sentinel/EvidenceManifest/v1":
        failures.append(
            f"manifest schema {manifest.get('schema')!r} is not api-sentinel/EvidenceManifest/v1"
        )
    entries = manifest.get("files") or []
    if not isinstance(entries, list):
        failures.append("manifest 'files' is not a list")
        entries = []

    # 1 — every manifest entry verifies byte-for-byte
    manifest_paths: set[str] = set()  # forward-slash normalized
    for entry in entries:
        if not isinstance(entry, dict):
            failures.append("manifest entry is not an object")
            continue
        rel, target = _relative_target(root, entry.get("path"))
        if rel is None or target is None:
            failures.append(f"{entry.get('path')!r}: invalid or escaping path")
            continue
        if rel in manifest_paths:
            failures.append(f"{rel}: duplicate manifest path")
            continue
        manifest_paths.add(rel)
        if not target.is_file():
            failures.append(f"{rel}: missing on disk")
            continue
        size = target.stat().st_size
        declared_size = entry.get("bytes")
        if not isinstance(declared_size, int) or declared_size < 0:
            failures.append(f"{rel}: invalid byte size")
            continue
        if size > MAX_FILE_BYTES:
            failures.append(f"{rel}: file exceeds {MAX_FILE_BYTES} bytes")
            continue
        digest = _sha256(target)
        if digest != entry.get("sha256"):
            failures.append(f"{rel}: sha256 mismatch ({entry.get('sha256')} != {digest})")
        if size != declared_size:
            failures.append(f"{rel}: byte size mismatch ({declared_size} != {size})")

    if not isinstance(receipt, dict):
        failures.append("receipt must be a JSON object")
        receipt = {}
    steps = receipt.get("steps") or []
    if not isinstance(steps, list):
        failures.append("receipt 'steps' is not a list")
        steps = []
    step_ids: list[str] = []
    for s in steps:
        if not isinstance(s, dict):
            failures.append("receipt step is not an object")
            continue
        sid = str(s.get("id") or "")
        if not sid.startswith("S") or not sid[1:].isdigit():
            failures.append(f"invalid step id: {sid!r}")
        if s.get("status") not in VALID_STATUSES:
            failures.append(f"step {sid}: invalid status {s.get('status')!r}")
        if s.get("truth") not in VALID_TRUTHS:
            failures.append(f"step {sid}: invalid truth {s.get('truth')!r}")
        step_ids.append(sid)
    if len(step_ids) != len(set(step_ids)):
        failures.append("receipt contains duplicate step ids")

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
        _, target = _relative_target(root, rel)
        if target is None:
            failures.append(f"steps/{sid}.json: invalid manifest path")
            continue
        try:
            on_disk = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            failures.append(f"steps/{sid}.json unreadable: {exc}")
            continue
        if on_disk != s:
            failures.append(f"step {sid}: receipt record != step file")

    # 4 — no step file on disk goes unmanifested
    steps_dirs = {
        (root / rel).parent
        for rel in manifest_paths
        if Path(rel).parts and Path(rel).parts[0] == "steps"
    }
    for d in steps_dirs:
        for f in d.glob("S*.json"):
            try:
                rel_f = str(f.resolve().relative_to(root)).replace("\\", "/")
            except ValueError:
                failures.append(f"{f}: step file escapes root")
                continue
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
            failures.append(f"summary.{key}: declared {summary.get(key)} != actual {want}")

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
