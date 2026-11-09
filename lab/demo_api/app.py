"""lab/demo_api — the real lab application the golden path observes.

A small FastAPI service (OpenAPI at /openapi.json) that:

- serves ``GET /work`` with measurable per-request latency and a hard
  dependency call: when the dependency (``lab/demo_api/dependency.py``)
  is down/failing/slow, ``/work`` answers 503 / slow 200 — a reproducible
  degradation the Sentinel can actually observe;
- records every request into 60-second metric windows (route x status
  code → latency histogram) and exports them as real
  ``api-sentinel/RuntimeObservation/v1`` JSONL at ``GET /metrics/export``;
- tags responses with a deploy version (``POST /deploy`` flips it live and
  appends to ``deploy-log.jsonl`` — converted to a DeployManifest for
  ``changes import``);
- writes sanitized access logs (method/route/status/duration — never
  query strings, headers, or bodies);
- runs as a real process the runner starts, polls, and kills with
  verified cleanup.

Run:

    python lab/demo_api/app.py --port 8320 --dep http://127.0.0.1:8330 \
        --log-dir lab/run/demo --version v1.0.0
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

BUCKET_BOUNDS_MS = (50.0, 100.0, 200.0, 500.0)  # +Inf implied
DEP_TIMEOUT_S = 2.0


class Metrics:
    """60s windowed latency histograms per (route, status)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # win_start_epoch -> route -> status -> list of (le, count) buckets
        self._windows: dict[int, dict[str, dict[int, dict[str, float]]]] = (
            defaultdict(lambda: defaultdict(lambda: defaultdict(
                lambda: {"count": 0.0, "sum": 0.0,
                         "b50": 0.0, "b100": 0.0, "b200": 0.0, "b500": 0.0}
            )))
        )

    def record(self, route: str, status: int, latency_ms: float) -> None:
        win = int(time.time() // 60) * 60
        with self._lock:
            cell = self._windows[win][route][status]
            cell["count"] += 1.0
            cell["sum"] += latency_ms
            for bound, key in zip(
                BUCKET_BOUNDS_MS, ("b50", "b100", "b200", "b500"), strict=True
            ):
                if latency_ms <= bound:
                    cell[key] += 1.0

    def export_observations(
        self, service_id: str, source_id: str
    ) -> list[dict[str, object]]:
        """Emit `api-sentinel/RuntimeObservation/v1` dicts (delta temporality).

        Only *closed* windows export — the current minute stays open until
        it ends, so a later re-export of the same window is byte-identical
        and the Sentinel dedups it as a no-op (never a conflict).
        """
        out: list[dict[str, object]] = []
        now_win = int(time.time() // 60) * 60
        with self._lock:
            snapshot = {
                win: {route: dict(codes) for route, codes in routes.items()}
                for win, routes in self._windows.items()
                if win < now_win
            }
        for win in sorted(snapshot):
            start = datetime.fromtimestamp(win, tz=UTC)
            end = datetime.fromtimestamp(win + 60, tz=UTC)
            for route in sorted(snapshot[win]):
                for status in sorted(snapshot[win][route]):
                    cell = snapshot[win][route][status]
                    n = cell["count"]
                    if n <= 0:
                        continue
                    route_key = route.strip("/").replace("/", "_") or "root"
                    out.append({
                        "schema": "api-sentinel/RuntimeObservation/v1",
                        "observation_id": f"demo_{win}_{route_key}_{status}",
                        "service_id": service_id,
                        "operation_id": f"GET {route}",
                        "release_id": STATE["version"],
                        "window": {
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                        },
                        "source_id": source_id,
                        "quality": "COMPLETE",
                        "measurement": {
                            "kind": "histogram",
                            "unit": "ms",
                            "count": int(n),
                            "sum": round(cell["sum"], 3),
                            "buckets": [
                                {"le": 50, "count": int(cell["b50"])},
                                {"le": 100, "count": int(cell["b100"])},
                                {"le": 200, "count": int(cell["b200"])},
                                {"le": 500, "count": int(cell["b500"])},
                                {"le": "+Inf", "count": int(n)},
                            ],
                        },
                        "attributes": {
                            "http.route": route,
                            "http.request.method": "GET",
                            "http.response.status_code": status,
                            "deployment.version": STATE["version"],
                        },
                    })
        return out


STATE: dict[str, str] = {"version": "v0.0.0", "dep_base": "", "log_dir": "."}
METRICS = Metrics()
app = FastAPI(title="sentinel-demo-api", version="1.0.0")


def _dep_health() -> tuple[bool, float, str]:
    """Call the real dependency — (ok, latency_ms, detail)."""
    base = STATE["dep_base"].rstrip("/")
    if not base:
        return False, 0.0, "no dependency configured"
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(  # noqa: S310 — the dep URL is a
            f"{base}/health", timeout=DEP_TIMEOUT_S) as r:  # runner arg
            ok = r.status == 200
            return ok, (time.perf_counter() - t0) * 1000.0, f"dep status {r.status}"
    except urllib.error.HTTPError as exc:
        return False, (time.perf_counter() - t0) * 1000.0, f"dep status {exc.code}"
    except OSError as exc:
        return False, (time.perf_counter() - t0) * 1000.0, f"dep error {exc}"


@app.middleware("http")
async def record_and_log(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000.0
    route = request.url.path  # no query string — sanitized by construction
    METRICS.record(route, response.status_code, ms)
    try:
        line = (
            f"{datetime.now(UTC).isoformat()} {request.method} {route} "
            f"{response.status_code} {ms:.1f}ms\n"
        )
        with (Path(STATE["log_dir"]) / "demo-access.log").open("a") as fh:
            fh.write(line)
    except OSError:
        pass
    return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": STATE["version"]}


@app.get("/work")
def work(response: Response) -> dict[str, object]:
    """Real work endpoint — deterministic jitter + the dependency gate."""
    # small deterministic jitter so latency is measurable but stable
    time.sleep(0.002 + (int(time.time() * 1000) % 5) * 0.0005)
    ok, dep_ms, detail = _dep_health()
    response.headers["X-Deploy-Version"] = STATE["version"]
    if not ok:
        response.status_code = 503
        return {
            "detail": "dependency unavailable",
            "dependency": detail,
            "version": STATE["version"],
        }
    return {
        "detail": "ok",
        "dependency": detail,
        "dep_latency_ms": round(dep_ms, 1),
        "version": STATE["version"],
    }


@app.get("/slow")
def slow() -> dict[str, str]:
    time.sleep(0.15)
    return {"detail": "ok", "version": STATE["version"]}


@app.post("/deploy")
async def deploy(request: Request) -> dict[str, object]:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"detail": "bad json"}, status_code=400)
    version = str(payload.get("version", "")).strip()
    if not version:
        return JSONResponse({"detail": "version required"}, status_code=400)
    STATE["version"] = version
    record = {"at": datetime.now(UTC).isoformat(), "kind": "deploy",
              "version": version}
    with (Path(STATE["log_dir"]) / "deploy-log.jsonl").open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return {"version": version, "at": record["at"]}


@app.get("/metrics/export")
def metrics_export(service_id: str, source_id: str) -> PlainTextResponse:
    """Export recorded windows as RuntimeObservation/v1 JSONL.

    The caller supplies the *registered* Sentinel ids — the demo never
    invents service/source identities.
    """
    lines = [
        json.dumps(o)
        for o in METRICS.export_observations(service_id, source_id)
    ]
    return PlainTextResponse(
        "\n".join(lines) + ("\n" if lines else ""),
        media_type="application/x-ndjson",
    )


@app.get("/metrics/snapshot")
def metrics_snapshot() -> dict[str, object]:
    obs = METRICS.export_observations("(unmapped)", "(unmapped)")
    return {"windows": len(obs), "version": STATE["version"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8320)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--dep", default="http://127.0.0.1:8330")
    ap.add_argument("--log-dir", type=Path, default=Path())
    ap.add_argument("--version", default="v1.0.0")
    args = ap.parse_args()
    args.log_dir.mkdir(parents=True, exist_ok=True)
    STATE["dep_base"] = args.dep
    STATE["log_dir"] = str(args.log_dir)
    STATE["version"] = args.version
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
