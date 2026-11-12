"""Controllable upstream dependency for lab/demo_api — real HTTP process.

A tiny stdlib service whose behavior the runner flips at runtime:

- ``GET /health`` → ``200 {"status": "ok"}`` normally;
  ``fail`` mode → ``503``; ``slow`` mode → ~250 ms then ``200``;
  process down → connection refused (the real "dependency down" case).
- ``POST /control {"mode": "ok|fail|slow"}`` flips the mode at runtime —
  the demo API's ``/work`` turns dependency failures into its own 503s,
  which is what the golden path degrades and recovers.

Run:  python lab/demo_api/dependency.py --port 8330 --log-dir lab/run/dep
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_MODE = "ok"
_LOCK = threading.Lock()


def _mode() -> str:
    with _LOCK:
        return _MODE


def _set_mode(mode: str) -> None:
    global _MODE
    with _LOCK:
        _MODE = mode


class Handler(BaseHTTPRequestHandler):
    log_dir: Path = Path()

    def log_message(self, *args: object) -> None:  # quiet stderr
        pass

    def _write(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self._log(status)

    def _log(self, status: int) -> None:
        # sanitized access line: method + route + status — never headers/query
        try:
            line = (
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                f"{self.command} {self.path.split('?')[0]} {status}\n"
            )
            with (self.log_dir / "dependency-access.log").open("a") as fh:
                fh.write(line)
        except OSError:
            pass

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            mode = _mode()
            if mode == "fail":
                return self._write(503, {"status": "failing", "mode": mode})
            if mode == "slow":
                time.sleep(0.25)
            return self._write(200, {"status": "ok", "mode": mode})
        if self.path == "/":
            return self._write(200, {"service": "demo-dependency", "mode": _mode()})
        return self._write(404, {"detail": "not found"})

    def do_POST(self) -> None:
        if self.path.startswith("/control"):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._write(400, {"detail": "bad json"})
            mode = str(payload.get("mode", ""))
            if mode not in ("ok", "fail", "slow"):
                return self._write(400, {"detail": "mode must be ok|fail|slow"})
            _set_mode(mode)
            return self._write(200, {"mode": mode})
        return self._write(404, {"detail": "not found"})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8330)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--log-dir", type=Path, default=Path())
    args = ap.parse_args()
    args.log_dir.mkdir(parents=True, exist_ok=True)
    Handler.log_dir = args.log_dir
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"dependency listening on {args.host}:{args.port}")  # noqa: T201
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
