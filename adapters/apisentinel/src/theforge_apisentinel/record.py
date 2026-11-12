"""``python -m theforge_apisentinel.record``: rewrite ``native_surface.json``.

Runs in the specialist's interpreter (``api_sentinel`` importable): records
the *public seam* surface the adapter relies on — the `SentinelBoundary`
methods and the handoff-translate functions — plus the live-catalog
verification: it drives the real `describe()`/`capabilities()` and records
which of the five declared capability ids the runtime catalog returns as
``supported``. That verification is the run evidence the acceptance
criterion requires — a semantic collision would surface as a missing or
non-supported id, never a workaround.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from theforge_apisentinel.catalog import (
    EXPECTED_CAPABILITIES,
    SNAPSHOT_PATH,
    VERIFICATION_PATH,
    native_fingerprint,
    validate_snapshot,
)

_SEAMS = (
    ("sentinel_boundary", "api_sentinel.integrations.the_forge.boundary", "SentinelBoundary"),
    (
        "handoff_translate",
        "api_sentinel.integrations.the_forge.handoff",
        "handoff_to_forge_request",
    ),
    ("handoff_response", "api_sentinel.integrations.the_forge.handoff", "response_to_result"),
)

BOUNDARY_METHODS = ("describe", "health", "capabilities", "execute", "handle")


def _seam(name: str, module_name: str, callable_name: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "name": name,
        "module": module_name,
        "callable": callable_name,
        "present": False,
    }
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return record
    target = getattr(module, callable_name, None)
    if target is None:
        return record
    record["present"] = True
    if name == "sentinel_boundary":
        record["methods"] = sorted(
            m for m in BOUNDARY_METHODS if callable(getattr(target, m, None))
        )
    else:
        try:
            record["parameters"] = sorted(inspect.signature(target).parameters)
        except (TypeError, ValueError):
            record["parameters"] = []
    return record


def capture() -> dict[str, Any]:
    """The native surface snapshot of THIS interpreter's api_sentinel."""
    import api_sentinel

    seams = [_seam(n, m, c) for n, m, c in _SEAMS]
    return {
        "specialist_version": str(getattr(api_sentinel, "__version__", "")),
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provenance": "recorded",
        "protocol_version": 1,
        "seams": seams,
    }


def verify_catalog() -> dict[str, Any]:
    """Drive the live boundary catalog and verify the five declared ids.

    Returns the verification record — which ids the runtime catalog reports
    ``supported``. A missing/non-supported id is a collision signal for the
    owner, never silently mapped.
    """
    from api_sentinel.integrations.the_forge.boundary import SentinelBoundary

    boundary = SentinelBoundary()
    manifest = boundary.describe()
    live = {c["id"]: c.get("state") for c in manifest.get("capabilities", [])}
    verification: dict[str, Any] = {
        "verified_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider": manifest.get("id", "api-sentinel-forge"),
        "expected": list(EXPECTED_CAPABILITIES),
        "live_states": {cid: live.get(cid) for cid in EXPECTED_CAPABILITIES},
        "extra_live_ids": sorted(cid for cid in live if cid not in EXPECTED_CAPABILITIES),
        "collisions": [cid for cid in EXPECTED_CAPABILITIES if live.get(cid) != "supported"],
    }
    verification["all_supported"] = not verification["collisions"]
    return verification


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m theforge_apisentinel.record")
    parser.add_argument(
        "--out",
        default=None,
        help="write the snapshot here instead of the packaged one",
    )
    args = parser.parse_args(argv)
    fresh = capture()
    validate_snapshot(fresh)
    target = Path(args.out) if args.out else SNAPSHOT_PATH
    target.write_text(
        json.dumps(fresh, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"recorded native surface {fresh['specialist_version']} -> {target}")
    try:
        verification = verify_catalog()
    except Exception as exc:
        print(f"catalog verification failed: {exc}", file=sys.stderr)
        return 1
    VERIFICATION_PATH.write_text(
        json.dumps(verification, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    supported = sum(1 for v in verification["live_states"].values() if v == "supported")
    print(
        f"catalog verification: {supported}/{len(EXPECTED_CAPABILITIES)} "
        f"supported -> {VERIFICATION_PATH}"
    )
    if not verification["all_supported"]:
        print(
            f"WARNING: capability ids not supported: {verification['collisions']}",
            file=sys.stderr,
        )
    print(f"native fingerprint {native_fingerprint(fresh)[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
