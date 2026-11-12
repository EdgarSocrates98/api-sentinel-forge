"""Private worker entry — ``python -m api_sentinel.integrations.doctor._worker``.

Runs one Doctor delegation inside a dedicated child process so the parent
can bound total wall-clock and terminate on breach (C101-F001): a blocked
Doctor can never stall a Sentinel investigation again.

Protocol (argv, never shell strings):

    _worker.py <request.json> <out_dir>

- ``request.json`` — a ForgeRequest-shaped dict + ``context_root``.
- ``out_dir`` — parent-created scratch dir; the worker writes
  ``bundle.json`` (the Doctor bundle, size-capped) and ``meta.json``
  (worker-side outcome record).

Exit codes are the contract — the parent maps them to named result
statuses, never to fabricated bundles:

- ``0``  completed — ``bundle.json`` written within the size cap.
- ``2``  usage — argv/request unreadable (INVALID_REQUEST).
- ``3``  ``forge_doctor_api`` not importable (PROVIDER_UNAVAILABLE).
- ``4``  invalid Doctor response (INVALID_RESPONSE).
- ``5``  Doctor raised / crashed (PROVIDER_FAILED).
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Any

MAX_BUNDLE_BYTES = 8 * 1024 * 1024  # 8 MiB — bounded output contract
MAX_REQUEST_BYTES = 4 * 1024 * 1024  # 4 MiB — bounded input contract

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3
EXIT_INVALID_RESPONSE = 4
EXIT_FAILED = 5


def _write_meta(out_dir: Path, record: dict[str, Any]) -> None:
    """Best-effort worker-side outcome record — never the error channel."""
    with contextlib.suppress(OSError):
        (out_dir / "meta.json").write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8"
        )


def _read_request(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError(f"request exceeds {MAX_REQUEST_BYTES} bytes")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("request payload must be a JSON object")
    request = data.get("request")
    context_root = data.get("context_root")
    if not isinstance(request, dict) or not isinstance(context_root, str):
        raise ValueError("request requires 'request' object and 'context_root'")
    return {"request": request, "context_root": context_root}


def _run(request: dict[str, Any], context_root: Path, out_dir: Path) -> int:
    from forge_doctor_api.handoff.boundary import DoctorBoundary
    from forge_doctor_api.handoff.protocol import ForgeRequest

    try:
        from forge_doctor_api.core.context import ProjectContext

        make_context = ProjectContext.from_root
    except ImportError:  # legacy <0.3 layout — same seam the adapter uses
        import importlib

        legacy = importlib.import_module("forge_doctor_api.handoff.context")
        make_context = legacy.ProjectContext.from_root

    parsed = ForgeRequest.parse(request)
    boundary = DoctorBoundary(context=make_context(context_root))
    bundle = boundary.handle(parsed)
    raw = bundle.to_dict()
    try:
        payload = json.dumps(raw, default=str)
    except (TypeError, ValueError):
        _write_meta(
            out_dir,
            {"status": "invalid_response", "detail": "bundle.to_dict() not JSON-serializable"},
        )
        return EXIT_INVALID_RESPONSE
    blob = payload.encode("utf-8")
    if len(blob) > MAX_BUNDLE_BYTES:
        _write_meta(
            out_dir,
            {"status": "invalid_response", "detail": f"bundle exceeds {MAX_BUNDLE_BYTES} bytes"},
        )
        return EXIT_INVALID_RESPONSE
    (out_dir / "bundle.json").write_bytes(blob)
    _write_meta(out_dir, {"status": "completed", "bundle_bytes": len(blob)})
    return EXIT_OK


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write("usage: _worker.py <request.json> <out_dir>\n")
        return EXIT_USAGE
    request_path, out_dir = Path(argv[1]), Path(argv[2])
    try:
        payload = _read_request(request_path)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        _write_meta(
            out_dir, {"status": "invalid_request", "detail": f"{type(exc).__name__}: {exc}"[:400]}
        )
        return EXIT_USAGE
    import importlib.util

    if importlib.util.find_spec("forge_doctor_api") is None:
        _write_meta(out_dir, {"status": "unavailable", "detail": "forge_doctor_api not importable"})
        return EXIT_UNAVAILABLE
    context_root = Path(payload["context_root"])
    try:
        return _run(payload["request"], context_root, out_dir)
    except Exception as exc:
        _write_meta(out_dir, {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"[:400]})
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
