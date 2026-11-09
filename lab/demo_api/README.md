# lab/demo_api — real lab application (Cycle 10 golden path)

A real HTTP service + a controllable upstream dependency, both running as
real OS processes. Used by `lab/federated_e2e.py` (S3–S6, S15) and
runnable standalone.

## Processes

```bash
# dependency (controllable): ok | fail | slow via POST /control
.venv/Scripts/python.exe lab/demo_api/dependency.py --port 8330 --log-dir lab/run/dep

# demo API (FastAPI): OpenAPI at /openapi.json, /work calls the dependency
.venv/Scripts/python.exe lab/demo_api/app.py --port 8320 \
    --dep http://127.0.0.1:8330 --log-dir lab/run/demo --version v1.0.0
```

## Surface

| Endpoint | Behavior |
|---|---|
| `GET /healthz` | liveness + version |
| `GET /work` | ~2-4ms + dependency gate → 503 when dep fails/down, slow when dep slow |
| `GET /slow` | ~150ms — latency-drift source |
| `POST /deploy {"version":"vX"}` | flips `X-Deploy-Version`, appends `deploy-log.jsonl` |
| `GET /metrics/export?service_id=..&source_id=..` | closed 60s windows as `RuntimeObservation/v1` JSONL (delta temporality) |
| `GET /metrics/snapshot` | window count probe (unmapped ids) |
| `POST /control` on the **dependency** | `{"mode":"ok|fail|slow"}` |
| `GET /openapi.json` | real FastAPI OpenAPI |

## Sanitization

`demo-access.log` / `dependency-access.log` record `ts method route status
duration_ms` only — never query strings, headers, or bodies.

## Metrics contract

One observation per (60s window, route, status code):
`measurement.kind=histogram`, `unit=ms`, cumulative buckets
`le ∈ {50,100,200,500,+Inf}`, `count`, `sum`, `temporality=delta`;
attributes carry `http.route`, `http.request.method`,
`http.response.status_code`, `deployment.version`. `observation_id` is
deterministic per (window, route, status) — closed-window re-export is
byte-identical, so Sentinel dedups it.
