"""Portable bootstrap installer for API Sentinel Forge.

Thin shim kept dependency-free (stdlib only): it creates or locates a
Python 3.11+ interpreter, installs the package editable when running from
a checkout, then delegates to ``api-sentinel install`` for the guided,
governed part of the setup. The heavy UX lives in the CLI, not here.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

MIN_VERSION = (3, 11)


def _run(cmd: list[str]) -> int:
    print("+", " ".join(cmd))
    return subprocess.call(cmd)  # noqa: S603 — argv list built from fixed values below


def main() -> int:
    parser = argparse.ArgumentParser(description="API Sentinel Forge portable installer")
    parser.add_argument(
        "--no-editable",
        action="store_true",
        help="Skip editable install of the local checkout (assumes api-sentinel already installed)",
    )
    parser.add_argument("--yes", action="store_true", help="Accept defaults (non-interactive)")
    args, passthrough = parser.parse_known_args()

    if sys.version_info < MIN_VERSION:
        print(
            f"api-sentinel-forge requires Python {MIN_VERSION[0]}.{MIN_VERSION[1]}+; "
            f"found {sys.version.split()[0]}",
            file=sys.stderr,
        )
        return 2

    checkout = Path(__file__).resolve().parent.parent
    if not (checkout / "pyproject.toml").is_file():
        print(f"pyproject.toml not found under {checkout}", file=sys.stderr)
        return 2

    if not args.no_editable:
        code = _run([sys.executable, "-m", "pip", "install", "-e", str(checkout)])
        if code != 0:
            return code

    sentinel_args = ["api-sentinel", "install"]
    if args.yes:
        sentinel_args.append("--yes")
    sentinel_args.extend(passthrough)
    return _run(sentinel_args)


if __name__ == "__main__":
    raise SystemExit(main())
