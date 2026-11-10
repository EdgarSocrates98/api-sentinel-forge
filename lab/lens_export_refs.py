"""Export a Forge Lens refs manifest for the Sentinel's FileLensACL.

Runs the *real* `forge-lens traces list --json` inside an initialized
Lens project directory (one that contains `.forge-lens/`) and maps each
stored trace to a reference record — the only shape the Sentinel
consumes (`integrations.lens.refs`):

    {"refs": [{trace_id, session_id?, service_id?, at?, provenance}]}

Trace payloads, spans and prompts are *never* exported — references
only; Forge Lens remains the owner of trace storage. This is the
canonical bridge for the Sentinel-side `FileLensACL` (Cycle 10.1
C101-F005): `Sentinel Incident -> Bridge Session -> Lens Trace ->
Evidence Reference`.

Usage:
    python lab/lens_export_refs.py \
        --lens-exe <path/to/forge-lens> --project-dir <dir> \
        [--project <id>] --out refs.manifest.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_TIMEOUT_S = 60.0


def export_refs(
    *,
    lens_exe: str,
    project_dir: Path,
    project: str | None,
    out: Path,
) -> dict[str, object]:
    cmd = [lens_exe, "traces", "list", "--json", "-n", "200"]
    if project:
        cmd += ["--project", project]
    proc = subprocess.run(  # noqa: S603 - fixed local executable + argv list
        cmd,
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"forge-lens traces list failed ({proc.returncode}): "
            f"{proc.stderr.strip()[:400]}"
        )
    rows = json.loads(proc.stdout or "[]")
    refs: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("trace_id"):
            continue  # never name-guess a ref
        ref: dict[str, object] = {
            "trace_id": str(row["trace_id"]),
            "provenance": "observed",
        }
        if row.get("session_id"):
            ref["session_id"] = str(row["session_id"])
        if row.get("started_at"):
            ref["at"] = str(row["started_at"])
        refs.append(ref)
    manifest = {"refs": refs}
    out.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lens-exe", required=True, help="forge-lens executable")
    ap.add_argument(
        "--project-dir",
        required=True,
        type=Path,
        help="Lens project dir (contains .forge-lens/)",
    )
    ap.add_argument("--project", default=None, help="Lens project_id filter")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    manifest = export_refs(
        lens_exe=args.lens_exe,
        project_dir=args.project_dir,
        project=args.project,
        out=args.out,
    )
    sys.stdout.write(
        f"wrote {len(manifest['refs'])} ref(s) -> {args.out}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
