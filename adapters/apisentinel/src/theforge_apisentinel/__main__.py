"""Entry point: ``python -m theforge_apisentinel [options] <op>``.

The Sentinel's `SentinelBoundary` is already the forge/v1 surface — the
adapter is a thin stdio bridge: it reads a `forge/v1` Request from stdin,
delegates to `SentinelBoundary.handle()` and prints the Response. Ops
(``describe``/``health``/``capabilities``/``execute``) are dispatched by the
boundary; the shell only guards the envelope and produces a structured
``error`` on a non-JSON request — never a traceback.
"""

from __future__ import annotations

import json
import sys

from theforge_apisentinel import PROVIDER_ID, VERSION

OPS = ("describe", "health", "capabilities", "execute")


def _error_reply(code: str, detail: str) -> dict:
    return {
        "protocol": "forge/v1",
        "kind": "Response",
        "request_id": "unknown",
        "op": None,
        "producer": {"id": PROVIDER_ID, "version": VERSION},
        "status": "error",
        "payload": {},
        "limitations": [],
        "unknowns": [],
        "error": {"code": code, "detail": detail},
    }


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(json.dumps(_error_reply(
            "APISENT-ADAPTER-BAD-REQUEST",
            f"stdin is not a JSON request: {exc}",
        )))
        return 0
    if not isinstance(request, dict):
        print(json.dumps(_error_reply(
            "APISENT-ADAPTER-BAD-REQUEST", "request is not an object"
        )))
        return 0
    from api_sentinel.integrations.the_forge.boundary import SentinelBoundary

    response = SentinelBoundary().handle(request)
    print(json.dumps(response, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
