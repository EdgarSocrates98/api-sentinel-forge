# ruff: noqa: T201, S603, S607, S310 -- lab runner: stdout IS the report;
# subprocess + urllib against real spawned processes is the whole point.
"""Cycle 10 Wave 4 — federated E2E golden path (S1-S18).

Runs the REAL Sentinel toolchain end to end:

S1  clean-wheel bootstrap (uv build → fresh venv → wheel install, no src/PYTHONPATH)
S2  real demo processes start (dependency + FastAPI app, /openapi.json)
S3  workspace install + service/source registration
S4  healthy traffic bursts (real HTTP across a 60s window boundary)
S5  metrics export → sentinel ingest (real RuntimeObservation/v1)
S6  SLO define + evaluate on real windows
S7  degradation (dependency fail → demo 503s) + ingest
S8  detect → incident → investigate (bounded pack, real session request)
S9  real Forge Doctor (in-process DoctorBoundary, demo's own openapi as context)
S10 real Code Atlas (read-only atlas.db seam, structural correlate)
S11 real the-forge route (provider spawn → execute → receipts)
S12 Bridge — emitted session request; BLOCKED: no agent provider on this host
S13 Lens  — refs manifest absent; BLOCKED with operator instructions
S14 Portal — real served bundle + same-origin /sentinel proxy + real browser
S15 recovery (dep ok + deploy tag + fresh windows → indicators recover)
S16 backup create → restore → integrity + count parity
S17 §12 negative security battery (lab/security_battery.py)
S18 consolidated receipt + evidence manifest

Every step emits an oracle record with fields
{id, command, input, output, process, ts, status, commit, version,
 provider, workspace, artifacts, hashes, errors, limitations}
plus a `truth` label — LOCAL_REAL / INTEGRATION_REAL / FIXTURE /
SIMULATED / EXTERNAL_REAL / NOT_RUN / BLOCKED. Nothing is claimed
"REAL" indiscriminately: a handoff file is transport, never execution.

Run:  .venv/Scripts/python.exe lab/federated_e2e.py [--skip-traffic-sleep]
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from runtime import resolve_entrypoint, resolve_python

ROOT = Path(__file__).resolve().parent.parent
RUN_BASE = ROOT / "lab" / "run" / "fed"
RUN = RUN_BASE
DEMO_API = ROOT / "lab" / "demo_api"
FIXTURES = ROOT / "lab" / "fixtures"
EVIDENCE = ROOT / "docs" / "cycles" / "cycle-10.3" / "evidence"
VENV_PY = resolve_python(ROOT)
SKIP_TRAFFIC_SLEEP = False
BUNDLE_ROOT: Path | None = None


def _configured_path(env_name: str, fallback: Path) -> Path:
    """Resolve an optional checkout/tool without making host paths required."""
    value = os.environ.get(env_name)
    return Path(value) if value else fallback


def _configured_executable(
    env_name: str, command: str, fallback: Path
) -> Path:
    value = os.environ.get(env_name)
    if value:
        return Path(value)
    discovered = shutil.which(command)
    return Path(discovered) if discovered else fallback


SYSTEM_PY_PLAYWRIGHT = _configured_executable(
    "FORGE_PLAYWRIGHT_PYTHON", "python", Path(sys.executable)
)
PORTAL_REPO = _configured_path("FORGE_PORTAL_DIR", ROOT.parent / "forge-portal")
PORTAL_BUNDLE = PORTAL_REPO / "dist" / "forge-portal-app" / "browser"
THEFORGE = _configured_executable(
    "THEFORGE_BIN", "theforge",
    ROOT.parent / "the-forge" / ".venv" / "Scripts" / "theforge.exe",
)
AWS_EXE = _configured_executable(
    "AWS_CLI", "aws", ROOT / "missing-optional-aws-cli"
)
ATLAS_DB = _configured_path(
    "CODE_ATLAS_DB", Path.home() / ".local" / "code-atlas" / "atlas.db"
)
ATLAS_REPO_KEY = (
    "git-remote:https://github.com/EdgarSocrates98/api-sentinel-forge.git"
    "@lab/fixtures/atlas-repo"
)
ATLAS_SERVICE_IDENTITY = "service.app#app"  # indexed fixture api_service
EXPECTED_AWS_ACCOUNT = "702561771161"

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


DEMO_PORT = int(os.environ.get("FORGE_DEMO_PORT") or _free_port())
DEP_PORT = int(os.environ.get("FORGE_DEP_PORT") or _free_port())
SENTINEL_PORT = int(os.environ.get("FORGE_SENTINEL_PORT") or _free_port())
TOKEN = "fed-e2e-token-" + "0" * 32  # env-channel only; never in URL/DOM


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, timeout=15,
        )
        return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


COMMIT = _git_head()


@dataclass
class Proc:
    """A real spawned child with verified cleanup."""

    popen: subprocess.Popen
    name: str
    cmd: list[str]

    def alive(self) -> bool:
        return self.popen.poll() is None

    def kill(self, timeout: int = 10) -> int | None:
        if self.alive():
            self.popen.terminate()
            try:
                return self.popen.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.popen.kill()
                return self.popen.wait(timeout=10)
        return self.popen.returncode


PROCS: list[Proc] = []
ORACLE: list[dict[str, object]] = []
EXIT = contextlib.ExitStack()  # holds process log fds open for the run
STEPS_DIR = RUN / "steps"
ART_DIR = RUN / "artifacts"


@dataclass
class Step:
    sid: str
    name: str
    status: str = "not_run"
    truth: str = "NOT_RUN"
    record: dict[str, object] = field(default_factory=dict)
    ok: bool = True  # assertions

    def begin(self, **kw: object) -> None:
        self.record = {
            "id": self.sid,
            "command": kw.get("command", []),
            "input": kw.get("input", {}),
            "output": kw.get("output", {}),
            "process": kw.get("process", {}),
            "ts": utcnow(),
            "status": "running",
            "commit": COMMIT,
            "version": kw.get("version", ""),
            "provider": kw.get("provider", ""),
            "workspace": kw.get("workspace", ""),
            "artifacts": [],
            "hashes": {},
            "errors": [],
            "limitations": list(kw.get("limitations", [])),
        }
        self.status = "running"

    def note(self, **kw: object) -> None:
        self.record["output"].update(kw)  # type: ignore[union-attr]

    def artifact(self, path: Path, label: str = "") -> None:
        self.record["artifacts"].append(str(path))  # type: ignore[union-attr]
        if path.is_file():
            self.record["hashes"][label or path.name] = sha256(path)  # type: ignore[index]

    def check(self, cond: bool, label: str) -> bool:
        self.record["output"].setdefault("asserts", []).append(  # type: ignore[union-attr]
            {"check": label, "ok": bool(cond)}
        )
        if not cond:
            self.ok = False
            self.record["errors"].append(f"assert failed: {label}")  # type: ignore[union-attr]
        return cond

    def finish(self, status: str, truth: str, detail: str = "") -> None:
        self.status = status if self.ok else "fail"
        self.truth = truth
        self.record["status"] = self.status
        self.record["truth"] = truth
        if detail:
            self.record["output"]["detail"] = detail  # type: ignore[index]
        self.record["finished_at"] = utcnow()
        STEPS_DIR.mkdir(parents=True, exist_ok=True)
        (STEPS_DIR / f"{self.sid}.json").write_text(
            json.dumps(self.record, indent=2, default=str), encoding="utf-8"
        )
        ORACLE.append(self.record)
        mark = {"pass": "PASS", "fail": "FAIL", "blocked": "BLOCK",
                "not_run": "SKIP"}.get(self.status, self.status)
        line = f"[{self.sid:<4} {mark:<5} {truth:<16}] {self.name} {detail}"
        try:
            print(line)
        except UnicodeEncodeError:
            # Console codec (e.g. cp1252 under redirected stdio) can't take
            # the step's arrows — degrade the *rendering*, never the record.
            print(line.encode("ascii", "replace").decode("ascii"))


def run_cmd(
    args: list[str], *, cwd: Path | None = None, timeout: int = 300,
    env_extra: dict[str, str] | None = None,
) -> dict[str, object]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
    )
    out: dict[str, object] = {
        "argv": args, "rc": p.returncode,
        "stdout": p.stdout[-8000:], "stderr": p.stderr[-4000:],
    }
    with contextlib.suppress(json.JSONDecodeError, ValueError):
        out["json"] = json.loads(p.stdout)
    return out


def port_free(port: int) -> bool:
    """No live listener on 127.0.0.1:port (connect-based — Windows keeps
    closed sockets in TIME_WAIT, so a bind probe lies about cleanup)."""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def wait_closed(port: int, tries: int = 40) -> bool:
    for _ in range(tries):
        if port_free(port):
            return True
        time.sleep(0.25)
    return False


def wait_http(url: str, proc: Proc | None = None, tries: int = 80) -> bool:
    for _ in range(tries):
        if proc is not None and not proc.alive():
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


class CLI:
    """The boot-venv api-sentinel binary (wheel-installed, S1)."""

    path: Path | None = None

    @classmethod
    def run(cls, args: list[str], ws: Path | None, timeout: int = 180) -> dict:
        cmd = [str(cls.path), *args]
        if ws is not None:
            cmd += ["--workspace", str(ws)]
        return run_cmd(cmd, timeout=timeout)


def drive_traffic(
    step: Step, *, demo: str, seconds: float, per_second: float = 12.0,
    label: str = "traffic",
) -> dict[str, object]:
    """Real HTTP traffic: `per_second` /work calls for `seconds`."""
    stats: dict[str, object] = {
        "label": label, "planned_seconds": seconds, "requests": 0,
        "by_status": {}, "latencies_ms": [],
    }
    deadline = time.monotonic() + seconds
    interval = 1.0 / max(per_second, 0.1)
    with httpx.Client(base_url=demo, timeout=5.0) as http:
        while time.monotonic() < deadline:
            t0 = time.perf_counter()
            try:
                r = http.get("/work")
                code = r.status_code
            except httpx.HTTPError:
                code = 0
            stats["requests"] += 1  # type: ignore[operator]
            by = stats["by_status"]  # type: ignore[assignment]
            by[str(code)] = by.get(str(code), 0) + 1
            stats["latencies_ms"].append(round((time.perf_counter() - t0) * 1000, 1))  # type: ignore[union-attr]
            dt = interval - (time.perf_counter() - t0)
            if dt > 0:
                time.sleep(dt)
    lats = stats["latencies_ms"]  # type: ignore[assignment]
    stats["latency_p50_ms"] = round(sorted(lats)[len(lats) // 2], 1) if lats else 0
    stats["latency_p95_ms"] = (
        round(sorted(lats)[int(len(lats) * 0.95)], 1) if lats else 0
    )
    return stats


def export_and_ingest(
    step: Step, *, demo: str, ws: Path, service_id: str, source_id: str,
    tag: str,
) -> dict[str, object]:
    """GET /metrics/export → file → `ingest` — a real metrics pipeline."""
    url = f"{demo}/metrics/export?service_id={service_id}&source_id={source_id}"
    with urllib.request.urlopen(url, timeout=30) as r:
        payload = r.read()
    path = ART_DIR / f"export-{tag}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    step.artifact(path, f"export-{tag}.jsonl")
    n_lines = payload.count(b"\n")
    res = CLI.run(["ingest", "--source-id", source_id, "--file", str(path), "--json"], ws)
    out = {
        "export_url": url, "export_lines": n_lines,
        "ingest": res.get("json") if isinstance(res.get("json"), dict) else res.get("stdout"),
        "ingest_rc": res["rc"],
    }
    return out


def semantic_row_differences(
    before: dict[str, object], after: dict[str, object]
) -> list[str]:
    """Return table names whose complete row sets differ.

    The caller supplies snapshots with audit history already excluded. This
    deliberately catches same-count/different-content restores.
    """
    left = before.get("rows")
    right = after.get("rows")
    if not isinstance(left, dict) or not isinstance(right, dict):
        return ["<invalid-snapshot>"]
    differences: list[str] = []
    for table in sorted(set(left) | set(right)):
        if left.get(table) != right.get(table):
            differences.append(str(table))
    return differences


def wait_closed_window(min_extra_seconds: float = 2.0) -> float:
    """Sleep until the current 60s window closes (export only ships closed
    windows). Returns seconds slept."""
    if SKIP_TRAFFIC_SLEEP:
        return 0.0
    now = time.time()
    wait = (60 - now % 60) + min_extra_seconds
    time.sleep(wait)
    return wait


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def s1_bootstrap() -> Path:
    st = Step("S1", "clean-wheel bootstrap")
    st.begin(command="uv build → uv venv → uv pip install wheel[api,aws]",
             provider="api-sentinel-forge", workspace=str(RUN))
    try:
        if (RUN / "boot-venv").exists():
            shutil.rmtree(RUN / "boot-venv", ignore_errors=True)
        for old in (ROOT / "dist").glob("api_sentinel_forge-*.whl"):
            old.unlink()
        b = run_cmd(["uv", "build", "--wheel", "--out-dir", "dist"], cwd=ROOT, timeout=600)
        st.record["output"]["build_rc"] = b["rc"]
        wheels = sorted((ROOT / "dist").glob("api_sentinel_forge-*.whl"))
        st.check(bool(wheels), "wheel built")
        if wheels:
            st.record["hashes"]["wheel"] = sha256(wheels[-1])
        v = run_cmd(["uv", "venv", str(RUN / "boot-venv")], cwd=ROOT)
        boot_py = resolve_python(ROOT, venv=RUN / "boot-venv")
        st.check(boot_py.exists(), f"fresh venv python exists: {boot_py}")
        st.check(b["rc"] == 0 and v["rc"] == 0, "build+venv rc=0")
        if wheels and boot_py.exists():
            # the wheel + its extras AND the real Forge Doctor engine —
            # S9's in-process delegation needs `forge_doctor_api`
            # importable in the interpreter that runs the CLI.
            doctor_src = _configured_path(
                "FORGE_DOCTOR_DIR", ROOT.parent / "forge-doctor-api"
            )
            i = run_cmd(
                ["uv", "pip", "install", "--python", str(boot_py),
                 f"{wheels[-1]}[api,aws]", str(doctor_src)],
                cwd=ROOT, timeout=600)
            st.check(i["rc"] == 0, "wheel+extras+forge_doctor_api installed "
                                   "into boot venv")
            cli = resolve_entrypoint("api-sentinel", python=boot_py)
            st.check(cli.exists(), f"api-sentinel entrypoint exists: {cli}")
            ver = run_cmd([str(cli), "version"]) if cli.exists() else {"rc": 1, "stdout": ""}
            st.record["version"] = (ver.get("stdout") or "").strip()
            st.record["process"] = {
                "python": str(boot_py), "cli": str(cli),
                "isolation": "no PYTHONPATH; no src/ on sys.path (wheel only)",
            }
            probe = run_cmd(
                [str(boot_py), "-c",
                 "import api_sentinel,sys; print(api_sentinel.__file__); "
                 "print(any('api-sentinel-forge\\\\src' in p or "
                 "'api-sentinel-forge/src' in p for p in sys.path))"])
            st.record["output"]["import_probe"] = probe.get("stdout", "")
            st.check(
                "src" not in str(probe.get("stdout", "")).splitlines()[0],
                "import resolves inside boot venv site-packages, not src/")
            if cli.exists():
                CLI.path = cli
        st.finish("pass" if st.ok else "fail", "LOCAL_REAL",
                  f"wheel={st.record['hashes'].get('wheel', 'n/a')[:24]}…")
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "LOCAL_REAL")
    return resolve_python(ROOT, venv=RUN / "boot-venv")


def s2_demo_start(boot_py: Path) -> dict[str, object]:
    st = Step("S2", "demo processes start (dep + FastAPI app)")
    st.begin(command="dependency.py + app.py (uvicorn)", provider="demo_api",
             workspace=str(RUN))
    try:
        dep_log = RUN / "dep"
        demo_log = RUN / "demo"
        dep_log.mkdir(parents=True, exist_ok=True)
        demo_log.mkdir(parents=True, exist_ok=True)
        dep = Proc(
            subprocess.Popen(
                [str(boot_py), str(DEMO_API / "dependency.py"),
                 "--port", str(DEP_PORT), "--log-dir", str(dep_log)],
                stdout=EXIT.enter_context((dep_log / "dep.out").open("w")),
                stderr=subprocess.STDOUT,
            ), "dependency",
            [str(DEMO_API / "dependency.py"), "--port", str(DEP_PORT)])
        PROCS.append(dep)
        app = Proc(
            subprocess.Popen(
                [str(boot_py), str(DEMO_API / "app.py"),
                 "--port", str(DEMO_PORT),
                 "--dep", f"http://127.0.0.1:{DEP_PORT}",
                 "--log-dir", str(demo_log), "--version", "v1.0.0"],
                stdout=EXIT.enter_context((demo_log / "app.out").open("w")),
                stderr=subprocess.STDOUT,
            ), "demo-api",
            [str(DEMO_API / "app.py"), "--port", str(DEMO_PORT)])
        PROCS.append(app)
        ok_dep = wait_http(f"http://127.0.0.1:{DEP_PORT}/health", dep)
        ok_app = wait_http(f"http://127.0.0.1:{DEMO_PORT}/healthz", app)
        st.check(ok_dep, f"dependency /health up on {DEP_PORT}")
        st.check(ok_app, f"demo /healthz up on {DEMO_PORT}")
        openapi = run_cmd(
            [str(boot_py), "-c",
             f"import urllib.request,json; d=json.load(urllib.request.urlopen('http://127.0.0.1:{DEMO_PORT}/openapi.json'));print(len(d['paths']))"])
        paths = (openapi.get("stdout") or "").strip()
        st.check(paths.isdigit() and int(paths) >= 5,
                 f"real OpenAPI served ({paths} paths)")
        # save the demo's own spec — Doctor's context root in S9
        ctx = RUN / "doctor-ctx"
        ctx.mkdir(parents=True, exist_ok=True)
        spec = urllib.request.urlopen(
            f"http://127.0.0.1:{DEMO_PORT}/openapi.json", timeout=10
        ).read()
        (ctx / "openapi.json").write_bytes(spec)
        st.record["process"] = {
            "dep_pid": dep.popen.pid, "app_pid": app.popen.pid,
            "dep_port": DEP_PORT, "demo_port": DEMO_PORT,
        }
        st.note(openapi_paths=paths, work_probe="pending")
        st.finish("pass" if st.ok else "fail", "LOCAL_REAL",
                  f"pids dep={dep.popen.pid} app={app.popen.pid}")
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "LOCAL_REAL")
    return {"demo": f"http://127.0.0.1:{DEMO_PORT}", "dep": f"http://127.0.0.1:{DEP_PORT}"}


def s3_register(proj: Path) -> dict[str, str]:
    st = Step("S3", "workspace install + service/source registration")
    ws = proj / ".api-sentinel"
    st.begin(command="install --yes → services --register → sources --register",
             provider="api-sentinel-forge", workspace=str(ws))
    try:
        c1 = CLI.run(["install", "--yes", "--json"], ws)
        st.check(c1["rc"] == 0, f"install rc={c1['rc']}")
        c2 = CLI.run(["services", "--register", "demo-api", "--json"], ws)
        svc = ((c2.get("json") or {}).get("service_id") or "")
        st.check(bool(svc), f"service registered: {svc}")
        st.check((ws / "sentinel.db").exists(), "sentinel.db materialized")
        c3 = CLI.run(
            ["sources", "--register", "jsonl-file", "--service-id", svc, "--json"], ws)
        src = ((c3.get("json") or {}).get("source_id") or "")
        st.check(bool(src), f"source registered: {src}")
        # declare the Atlas structural identity on the service (same
        # mechanism Wave 2 used — a repository write, not a name guess)
        if svc:
            script = (
                "from pathlib import Path;import sys\n"
                "from api_sentinel.infrastructure.storage import SqliteUnitOfWork\n"
                "from api_sentinel.domain import ExternalRef\n"
                f"uow=SqliteUnitOfWork(r'{ws / 'sentinel.db'}')\n"
                "with uow as u:\n"
                f" s=u.services.get(r'{svc}')\n"
                " s=s.model_copy(update={'external_refs': s.external_refs + "
                f"[ExternalRef(kind='code_atlas_identity', ref=r'{ATLAS_SERVICE_IDENTITY}')]"
                "})\n"
                " u.services.put(s);u.commit()\n"
                "print('linked')\n"
            )
            boot_py = resolve_python(ROOT, venv=RUN / "boot-venv")
            w = run_cmd([str(boot_py), "-c", script])
            st.record["output"]["atlas_link"] = (w.get("stdout") or "").strip()
        st.note(service_id=svc, source_id=src)
        st.finish("pass" if st.ok else "fail", "LOCAL_REAL", f"svc={svc} src={src}")
        return {"service_id": svc, "source_id": src}
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "LOCAL_REAL")
        return {}


def s4_traffic(demo: str, traffic_seconds: float) -> dict[str, object]:
    st = Step("S4", "healthy traffic burst (real HTTP, crosses a window boundary)")
    st.begin(command=f"GET {demo}/work x{traffic_seconds}s @12rps",
             provider="demo_api")
    try:
        stats = drive_traffic(
            st, demo=demo, seconds=traffic_seconds, label="healthy")
        st.check(stats["requests"] > 60, f"{stats['requests']} real requests")
        st.check(stats["by_status"].get("200", 0) > 60,
                 f"healthy 2xx share ({stats['by_status']})")
        st.note(**stats)
        st.finish("pass" if st.ok else "fail", "LOCAL_REAL",
                  f"{stats['requests']} req p50={stats['latency_p50_ms']}ms")
        return stats
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "LOCAL_REAL")
        return {}


def s5_ingest(demo: str, ws: Path, ids: dict[str, str]) -> dict[str, object]:
    st = Step("S5", "metrics export → sentinel ingest")
    st.begin(command="GET /metrics/export → api-sentinel ingest --json",
             provider="demo_api+api-sentinel", workspace=str(ws))
    try:
        slept = wait_closed_window()
        st.note(waited_for_window_close_s=round(slept, 1))
        out = export_and_ingest(
            st, demo=demo, ws=ws,
            service_id=ids["service_id"], source_id=ids["source_id"],
            tag="healthy")
        ing = out.get("ingest") or {}
        st.check(out["ingest_rc"] == 0, "ingest rc=0")
        st.check((ing.get("accepted") or 0) >= 1,
                 f"accepted={ing.get('accepted')} skipped={ing.get('skipped_duplicates')}")
        st.note(**{k: v for k, v in out.items() if k != "ingest"})
        st.note(ingest=ing)
        st.finish("pass" if st.ok else "fail", "LOCAL_REAL",
                  f"accepted={ing.get('accepted')}")
        return out
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "LOCAL_REAL")
        return {}


def _win_bounds(minutes_ago_start: int, minutes_ago_end: int) -> tuple[str, str]:
    now = datetime.now(UTC)
    s = now.replace(second=0, microsecond=0)
    from datetime import timedelta
    return (
        (s - timedelta(minutes=minutes_ago_start)).isoformat(),
        (s - timedelta(minutes=minutes_ago_end)).isoformat(),
    )


def s6_slo(ws: Path, ids: dict[str, str]) -> dict[str, object]:
    st = Step("S6", "SLO define + evaluate (real windows)")
    st.begin(command="slo define --target 0.98 --window 15m → slo evaluate",
             provider="api-sentinel", workspace=str(ws))
    try:
        c = CLI.run(
            ["slo", "define", "--service-id", ids["service_id"],
             "--target", "0.98", "--window", "15m", "--json"], ws)
        slo_id = ((c.get("json") or {}).get("slo_id") or "")
        st.check(bool(slo_id), f"slo defined: {slo_id}")
        a, b = _win_bounds(10, 0)
        e = CLI.run(
            ["slo", "evaluate", "--slo-id", slo_id,
             "--window-start", a, "--window-end", b, "--json"], ws)
        d = e.get("json") or {}
        st.check(e["rc"] == 0, "slo evaluate rc=0")
        st.note(slo_id=slo_id, evaluate_window=[a, b],
                assessment={k: d.get(k) for k in ("state", "sli", "eligible", "good")})
        st.finish("pass" if st.ok else "fail", "LOCAL_REAL",
                  f"state={d.get('state')} sli={d.get('sli')}")
        return {"slo_id": slo_id, "assessment": d}
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "LOCAL_REAL")
        return {}


def s7_degrade(dep: str, demo: str, ws: Path, ids: dict[str, str],
               traffic_seconds: float) -> dict[str, object]:
    st = Step("S7", "degradation: dependency fail → demo 503s → ingest")
    st.begin(command=f"POST {dep}/control fail → traffic → export → ingest",
             provider="demo_api")
    try:
        req = urllib.request.Request(
            f"{dep}/control", data=b'{"mode":"fail"}',
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            ctl_body = r.read().decode()
        st.note(dep_control=ctl_body.strip())
        stats = drive_traffic(st, demo=demo, seconds=traffic_seconds,
                              label="degraded-503")
        share = stats["by_status"].get("503", 0) / max(stats["requests"], 1)  # type: ignore[operator]
        st.check(share > 0.9, f">90% requests 503 ({stats['by_status']})")
        slept = wait_closed_window()
        st.note(waited_for_window_close_s=round(slept, 1))
        out = export_and_ingest(
            st, demo=demo, ws=ws,
            service_id=ids["service_id"], source_id=ids["source_id"],
            tag="degraded")
        ing = out.get("ingest") or {}
        st.check(out["ingest_rc"] == 0, "ingest rc=0")
        st.note(traffic=stats, ingest=ing)
        st.finish("pass" if st.ok else "fail", "LOCAL_REAL",
                  f"{stats['requests']} req, {ing.get('accepted')} obs ingested")
        return {"stats": stats, "ingest": ing}
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "LOCAL_REAL")
        return {}


def s8_detect_investigate(ws: Path, ids: dict[str, str]) -> dict[str, str]:
    st = Step("S8", "detect → incident → investigate (bounded pack)")
    st.begin(command="detect → incidents → incident show → investigate",
             provider="api-sentinel", workspace=str(ws))
    try:
        a, b = _win_bounds(10, 0)
        now = datetime.now(UTC).isoformat()
        d = CLI.run(
            ["detect", "--service-id", ids["service_id"],
             "--window-start", a, "--window-end", b, "--now", now, "--json"], ws)
        rep = d.get("json") or {}
        anoms = rep.get("anomalies") or []
        kinds = sorted({
            (e.get("detector_id") or "?")
            for e in (rep.get("evaluations") or [])
            if e.get("outcome") == "fired"
        } or {
            (x.get("rule") or {}).get("rule_id") or "?" for x in anoms
        })
        st.check(d["rc"] == 0 and len(anoms) >= 1,
                 f"anomalies raised: {len(anoms)} ({kinds})")
        st.note(anomaly_kinds=kinds,
                evaluations_outcomes={
                    e.get("detector_id"): e.get("outcome")
                    for e in (rep.get("evaluations") or [])
                })
        incs = CLI.run(["incidents", "--json"], ws).get("json") or []
        inc = next(
            (i for i in incs if i.get("service_id") == ids["service_id"]),
            incs[0] if incs else {},
        )
        inc_id = str(inc.get("incident_id") or "")
        st.check(bool(inc_id), f"incident exists: {inc_id}")
        if inc_id:
            sh = CLI.run(["incident", "show", inc_id, "--json"], ws)
            st.check(sh["rc"] == 0, "incident show rc=0")
            inv = CLI.run(
                ["investigate", "--incident-id", inc_id,
                 "--provider", "claude", "--cost-limit", "2.0", "--json"], ws)
            invd = inv.get("json") or {}
            sess = (invd.get("session") or {})
            st.check(inv["rc"] == 0, "investigate rc=0")
            st.check(bool(invd.get("pack")), "bounded context pack emitted")
            st.note(investigation_status=sess.get("status"),
                    session_ref=sess.get("session_ref"))
        st.finish("pass" if st.ok else "fail", "LOCAL_REAL", f"inc={inc_id}")
        return {"incident_id": inc_id, "investigate": invd}
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "LOCAL_REAL")
        return {}


def s9_doctor(ws: Path, ids: dict[str, str]) -> None:
    st = Step("S9", "real Forge Doctor delegation (in-process boundary)")
    ctx = RUN / "doctor-ctx"
    st.begin(command=f"handoff doctor --service-id {ids['service_id']} "
                     f"--context-root {ctx} (demo openapi as the real spec)",
             provider="forge-doctor-api", workspace=str(ws))
    try:
        if not (ctx / "openapi.json").is_file():
            st.check(False, "doctor-ctx openapi.json missing (S2 must save it)")
        else:
            c = CLI.run(
                ["handoff", "doctor", "--service-id", ids["service_id"],
                 "--context-root", str(ctx), "--json"], ws, timeout=300)
            d = c.get("json") or {}
            st.note(mode=d.get("mode"), status=d.get("status"),
                    detail=str(d.get("detail"))[:300])
            ok = c["rc"] == 0 and d.get("status") in (
                "completed", "delegated")
            mode_note = ("in-process DoctorBoundary"
                         if d.get("mode") == "in_process"
                         else "file transport")
            st.check(ok, f"doctor status={d.get('status')} "
                         f"(real {mode_note})")
            receipts = sorted((ws / "receipts").glob("**/*.json"))
            for r in receipts[-3:]:
                st.artifact(r, f"receipt:{r.name}")
            st.finish(
                "pass" if st.ok else "fail",
                "INTEGRATION_REAL" if d.get("mode") == "in_process" else "LOCAL_REAL",
                f"status={d.get('status')}")
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "INTEGRATION_REAL")


def s10_atlas(ws: Path, ids: dict[str, str], incident_id: str) -> None:
    st = Step("S10", "real Code Atlas structural correlate (read-only db seam)")
    st.begin(command=f"correlate --service-id {ids['service_id']} with "
                     f"integrations.atlas.db_path={ATLAS_DB} "
                     f"(incident context: {incident_id})",
             provider="code-atlas", workspace=str(ws))
    try:
        cfg = ws / "config.yaml"
        text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        if "atlas" not in text:
            text += (
                "\nintegrations:\n  atlas:\n"
                f"    db_path: {ATLAS_DB.as_posix()}\n"
                f"    repository: {ATLAS_REPO_KEY}\n"
            )
            cfg.write_text(text, encoding="utf-8")
        if not ATLAS_DB.is_file():
            st.record["limitations"].append(
                f"Code Atlas database unavailable at {ATLAS_DB}; no provider claim made"
            )
            st.note(structural_edges=0, status="BLOCKED",
                    reason="optional Code Atlas database unavailable")
            st.finish("blocked", "BLOCKED", "real atlas.db unavailable")
            return
        c = CLI.run(
            ["correlate", "--service-id", ids["service_id"], "--json"], ws)
        d = c.get("json") or {}
        structural = d.get("structural_edges") or []
        st.check(c["rc"] == 0, "correlate rc=0")
        st.check(len(structural) >= 1,
                 f"structural edges from real atlas.db: {len(structural)}")
        st.note(structural_edges=len(structural),
                sample=structural[:3],
                status=d.get("status"),
                unknowns=d.get("unknowns"))
        st.finish("pass" if st.ok else "fail", "INTEGRATION_REAL",
                  f"{len(structural)} structural edges")
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "INTEGRATION_REAL")


def s11_theforge(proj: Path) -> None:
    st = Step("S11", "real the-forge route → provider execute → receipts")
    st.begin(command="theforge init → theforge ask 'run api.anomaly-detection run'",
             provider="the-forge", workspace=str(proj))
    try:
        st.check(THEFORGE.exists(), f"theforge binary: {THEFORGE}")
        if not (proj / ".forge").exists():
            i = run_cmd([str(THEFORGE), "init"], cwd=proj, timeout=120)
            st.check(i["rc"] == 0, f"theforge init rc={i['rc']}: "
                                   f"{(i.get('stderr') or '')[:200]}")
        ask = run_cmd(
            [str(THEFORGE), "ask", "run api.anomaly-detection run",
             "--capability", "api.anomaly-detection", "--action", "run",
             "--json"], cwd=proj, timeout=300)
        st.note(ask_rc=ask["rc"], ask_out=(ask.get("stdout") or "")[-1200:])
        runs_dir = proj / ".forge" / "runs"
        runs = sorted(runs_dir.glob("*")) if runs_dir.exists() else []
        st.check(bool(runs), f"theforge run artifacts: {len(runs)}")
        if runs:
            latest = runs[-1]
            dst = ART_DIR / f"theforge-{latest.name}"
            shutil.copytree(latest, dst, dirs_exist_ok=True)
            for f in sorted(dst.glob("*.json")):
                st.artifact(f)
            result = dst / "result.json"
            if result.is_file():
                rd = json.loads(result.read_text(encoding="utf-8"))
                st.note(result_status=rd.get("status"),
                        findings=len(rd.get("findings") or []))
                st.check(rd.get("status") in ("ok", "success"),
                         f"provider execute status={rd.get('status')}")
        st.finish("pass" if st.ok else "fail", "INTEGRATION_REAL",
                  "provider executed through real forge/v1 bridge")
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "INTEGRATION_REAL")


def s12_bridge(ws: Path, inv: dict[str, object]) -> None:
    st = Step("S12", "Bridge session request — provider absent on this host")
    st.begin(command="investigate (S8) session transport state + import probe",
             provider="forge-agent-bridge", workspace=str(ws))
    try:
        boot_py = resolve_python(ROOT, venv=RUN / "boot-venv")
        probe = run_cmd(
            [str(boot_py), "-c",
             "import importlib.util;"
             "print(importlib.util.find_spec('forge_agent_bridge') is not None)"])
        importable = (probe.get("stdout") or "").strip() == "True"
        sess = (inv.get("investigate") or {}).get("session") or {}
        reqs = sorted((ws / "agent_sessions").glob("**/session_request.json"))
        for r in reqs:
            st.artifact(r, f"session_request:{r.parent.name}")
        st.note(
            bridge_importable=importable,
            session_status=sess.get("status"),
            session_ref=sess.get("session_ref"),
            emitted_requests=len(reqs),
        )
        st.record["limitations"].append(
            "agent provider execution is out of this wave's scope "
            "(Grill Gate: no real agent-provider session); the emitted "
            "session_request.json is transport, never execution")
        st.record["limitations"].append(
            "operator instructions for a real run: install forge-agent-bridge, "
            "then `api-sentinel investigate --incident-id <id> --provider <p>` "
            "— the Bridge owns execution, approvals and budget")
        st.finish("blocked", "BLOCKED",
                  f"status={sess.get('status') or 'unavailable'} "
                  f"(session_request emitted={bool(reqs)})")
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("blocked", "BLOCKED")


def s13_lens(ws: Path) -> None:
    st = Step("S13", "Lens trace refs — no provider/manifest on this host")
    st.begin(command="integrations status + forge_lens probe + refs manifest check",
             provider="forge-lens", workspace=str(ws))
    try:
        boot_py = resolve_python(ROOT, venv=RUN / "boot-venv")
        probe = run_cmd(
            [str(boot_py), "-c",
             "import importlib.util;"
             "print(importlib.util.find_spec('forge_lens') is not None)"])
        importable = (probe.get("stdout") or "").strip() == "True"
        integ = CLI.run(["integrations", "--json"], ws)
        st.note(forge_lens_importable=importable,
                integrations=integ.get("json") or (integ.get("stdout") or "")[:600])
        st.record["limitations"].append(
            "no forge-lens provider and no exported refs manifest on this "
            "host — trace refs are Lens-owned; absent → named unavailable, "
            "never fabricated")
        st.record["limitations"].append(
            "operator instructions: export a refs manifest via forge-lens, "
            "then set `integrations.lens.refs` in config.yaml")
        st.finish("blocked", "BLOCKED",
                  f"importable={importable}, no refs manifest configured")
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("blocked", "BLOCKED")


# --- S14: real Portal — served bundle + same-origin proxy + real browser ---

_HOP_BY_HOP = frozenset(
    h.lower() for h in (
        "connection", "keep-alive", "proxy-authenticate",
        "proxy-authorization", "te", "trailers", "transfer-encoding",
        "upgrade", "host", "cookie", "origin", "referer",
    )
)


def _proxy_handler(bundle: Path, sentinel_base: str, prefix: str = "/sentinel"):
    """The Wave-3 same-origin pattern: static portal + /sentinel proxy."""
    import http.server

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(bundle), **kw)

        def log_message(self, *a):
            pass

        def _write_payload(self, payload: bytes) -> None:
            """Ignore a client disconnect after the response is committed.

            Playwright may close a page immediately after receiving the
            assertion response.  On Windows that is reported as
            ``ConnectionAbortedError`` (and as a broken pipe/reset on Unix),
            none of which is a proxy failure or a reason to leave a noisy
            traceback in the E2E server log.
            """
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError,
                    ConnectionAbortedError):
                return

        def _proxy(self):
            path = self.path[len(prefix):] or "/"
            body = None
            if self.command == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b"{}"
            headers = {
                k: v for k, v in self.headers.items()
                if k.lower() not in _HOP_BY_HOP
            }
            req = urllib.request.Request(
                f"{sentinel_base}{path}", data=body, headers=headers,
                method=self.command)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    payload, status = r.read(), r.status
                    ctype = r.headers.get("Content-Type", "application/json")
            except urllib.error.HTTPError as e:
                payload, status = e.read(), e.code
                ctype = e.headers.get("Content-Type", "application/json")
            except OSError as e:
                payload = json.dumps(
                    {"title": "proxy error", "detail": str(e), "status": 502}
                ).encode()
                status, ctype = 502, "application/json"
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self._write_payload(payload)

        def do_GET(self):
            if self.path.startswith(prefix):
                return self._proxy()
            if self.path.startswith("/api/"):
                payload = json.dumps(
                    {"schema": "forge/PortalApi/1", "correlation_id": "e2e",
                     "error": {"code": "PF-UNAVAILABLE",
                               "message": "product forge not wired in this e2e"}}
                ).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self._write_payload(payload)
                return
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith(prefix):
                return self._proxy()
            self.send_error(404)

        def do_OPTIONS(self):
            self.send_response(204)
            self.end_headers()

        def send_head(self):
            path = self.translate_path(self.path)
            if self.path == "/" or (
                not Path(path).exists() and "." not in Path(self.path).name
            ):
                self.path = "/index.html"
            return super().send_head()

    return Handler


_PORTAL_PROBE = r"""
import json, sys
from playwright.sync_api import sync_playwright

base, proxy_base, token, shot_dir = sys.argv[1:5]
results = {"steps": [], "artifacts": []}
def step(name, ok, detail=""):
    results["steps"].append({"name": name, "ok": bool(ok), "detail": detail[:300]})

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.add_init_script("localStorage.setItem('forge-portal.locale','en')")
    page.goto(f"{base}/api-operations")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)
    page.get_by_role("button", name="Sentinel connection").first.click()
    page.locator(".conn input").nth(0).fill(proxy_base)
    page.locator(".conn input").nth(1).fill(token)
    page.get_by_role("button", name="Test connection").click()
    page.wait_for_selector(".conn .ok", timeout=15000)
    ok_text = page.locator(".conn .ok").inner_text()
    step("connection", "api-sentinel" in ok_text, ok_text)
    page.get_by_role("button", name="Save").click()
    page.wait_for_timeout(600)
    page.wait_for_selector("tr.row", timeout=15000)
    body = page.locator("body").inner_text()
    step("services render", "demo-api" in body, body[:300])
    page.screenshot(path=shot_dir + "/portal-services.png")
    results["artifacts"].append("portal-services.png")
    page.locator(".tab").nth(1).click()
    page.wait_for_selector("tr.row", timeout=15000)
    page.screenshot(path=shot_dir + "/portal-incidents.png")
    results["artifacts"].append("portal-incidents.png")
    step("incidents render", True, "")
    leaked = page.evaluate("(t)=>document.body.innerHTML.includes(t)", token)
    step("token not in DOM", not leaked, "")
    browser.close()
print(json.dumps(results))
"""


def s14_portal(ws: Path, ids: dict[str, str]) -> None:
    st = Step("S14", "real Portal: served bundle → same-origin proxy → browser → Sentinel")
    st.begin(command="serve sentinel ws → stdlib same-origin /sentinel proxy → "
                     "playwright (system python) through the real bundle",
             provider="forge-portal", workspace=str(ws))
    try:
        if not PORTAL_BUNDLE.is_dir():
            st.record["limitations"].append(
                f"Portal bundle unavailable at {PORTAL_BUNDLE}"
            )
            st.finish("blocked", "BLOCKED", "Portal bundle unavailable")
            return
        if not SYSTEM_PY_PLAYWRIGHT.exists():
            st.record["limitations"].append(
                f"browser runtime unavailable at {SYSTEM_PY_PLAYWRIGHT}"
            )
            st.finish("blocked", "BLOCKED", "browser runtime unavailable")
            return
        playwright_probe = run_cmd(
            [str(SYSTEM_PY_PLAYWRIGHT), "-c", "import playwright"], timeout=30
        )
        if playwright_probe["rc"] != 0:
            st.record["limitations"].append(
                "Playwright is not installed in the selected browser runtime"
            )
            st.note(playwright_probe=playwright_probe)
            st.finish("blocked", "BLOCKED", "Playwright runtime unavailable")
            return
        # real sentinel serve process
        env = dict(os.environ, API_SENTINEL_TOKEN=TOKEN,
                   PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        serve = Proc(
            subprocess.Popen(
                [str(CLI.path), "serve", "--host", "127.0.0.1",
                 "--port", str(SENTINEL_PORT), "--workspace", str(ws)],
                stdout=EXIT.enter_context((RUN / "serve.out").open("w")),
                stderr=subprocess.STDOUT,
                env=env),
            "sentinel-serve", ["serve", "--port", str(SENTINEL_PORT)])
        PROCS.append(serve)
        up = wait_http(f"http://127.0.0.1:{SENTINEL_PORT}/v1/health", serve)
        st.check(up, f"sentinel /v1 up on {SENTINEL_PORT}")
        # same-origin proxy — the CSP connect-src 'self' answer (C10-F019)
        import http.server
        handler = _proxy_handler(
            PORTAL_BUNDLE, f"http://127.0.0.1:{SENTINEL_PORT}")
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        portal_base = f"http://127.0.0.1:{srv.server_address[1]}"
        proxy_base = f"{portal_base}/sentinel"
        # probe the proxy path before the browser does (evidence)
        probe_req = urllib.request.Request(
            f"{proxy_base}/v1/services",
            headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(probe_req, timeout=10) as r:
            proxied = json.loads(r.read())
        # ServiceListResponse envelope: {"services": [{"service":
        # {"service_id": ...}, "health": ..., "coverage": ...}]}
        rows = proxied.get("services", proxied) if isinstance(proxied, dict) else proxied
        st.check(
            isinstance(rows, list) and any(
                (s.get("service") or s).get("service_id")
                == ids["service_id"]
                for s in rows if isinstance(s, dict)),
            "same-origin proxy → real /v1/services returns the demo service")
        shot_dir = ART_DIR / "portal"
        shot_dir.mkdir(parents=True, exist_ok=True)
        probe_script = RUN / "portal_probe.py"
        probe_script.write_text(_PORTAL_PROBE, encoding="utf-8")
        pw = run_cmd(
            [str(SYSTEM_PY_PLAYWRIGHT), str(probe_script),
             portal_base, proxy_base, TOKEN, str(shot_dir)],
            timeout=240)
        st.note(playwright_rc=pw["rc"])
        try:
            pwres = json.loads((pw.get("stdout") or "").strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            pwres = {"steps": [], "parse_error": (pw.get("stdout") or "")[:400]}
        st.note(playwright=pwres, playwright_err=(pw.get("stderr") or "")[-600:])
        for png in sorted(shot_dir.glob("*.png")):
            st.artifact(png, png.name)
        ok_steps = [s for s in pwres.get("steps", []) if s.get("ok")]
        st.check(len(ok_steps) >= 3 and all(
            s["ok"] for s in pwres.get("steps", [])),
            f"browser steps all ok ({len(ok_steps)}/{len(pwres.get('steps', []))})")
        st.note(portal_base=portal_base, proxy_base=proxy_base)
        st.finish("pass" if st.ok else "fail", "INTEGRATION_REAL",
                  f"browser steps {len(ok_steps)}/{len(pwres.get('steps', []))}")
        with contextlib.suppress(Exception):
            srv.shutdown()
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "INTEGRATION_REAL")


def s15_recovery(dep: str, demo: str, ws: Path, ids: dict[str, str],
                 traffic_seconds: float, slo: dict[str, object]) -> None:
    st = Step("S15", "recovery: dep ok + deploy tag → fresh windows → indicators recover")
    st.begin(command="POST /control ok → POST /deploy v1.1.0 → traffic → "
                     "export → ingest → slo evaluate + detect",
             provider="demo_api+api-sentinel")
    try:
        req = urllib.request.Request(
            f"{dep}/control", data=b'{"mode":"ok"}',
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            ctl_body = r.read().decode()
        req2 = urllib.request.Request(
            f"{demo}/deploy", data=b'{"version":"v1.1.0"}',
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req2, timeout=10) as r:
            dep_body = r.read().decode()
        st.note(dep_control=ctl_body.strip(), deploy=dep_body.strip())
        # record the deploy as a real change event (proximity ≠ cause)
        ch = CLI.run(
            ["changes", "record", "--service-id", ids["service_id"],
             "--kind", "deploy",
             "--at", datetime.now(UTC).isoformat(),
             "--ref", "v1.1.0", "--source", "lab-demo",
             "--detail", "version=v1.1.0,trigger=recovery", "--json"], ws)
        st.note(change_recorded=(ch.get("json") or {}).get("change_id"))
        # recovery indicators must bound to RECOVERY windows only —
        # a range that still covers the degraded minutes would honestly
        # report the breach again and prove nothing.
        rec_start = (
            datetime.now(UTC).replace(second=0, microsecond=0)
            .isoformat()
        )
        stats = drive_traffic(st, demo=demo, seconds=traffic_seconds,
                              label="recovery")
        share = stats["by_status"].get("200", 0) / max(stats["requests"], 1)  # type: ignore[operator]
        st.check(share > 0.95, f">95% requests 200 post-recovery ({stats['by_status']})")
        slept = wait_closed_window()
        st.note(waited_for_window_close_s=round(slept, 1))
        out = export_and_ingest(
            st, demo=demo, ws=ws,
            service_id=ids["service_id"], source_id=ids["source_id"],
            tag="recovery")
        ing = out.get("ingest") or {}
        st.check(out["ingest_rc"] == 0, "recovery ingest rc=0")
        # indicators recover: recovery-only window evaluates good / no new
        # error anomalies — the degraded window stays in the journal as
        # evidence, but must not leak into the recovery proof
        now_iso = datetime.now(UTC).isoformat()
        if slo.get("slo_id"):
            e = CLI.run(
                ["slo", "evaluate", "--slo-id", str(slo["slo_id"]),
                 "--window-start", rec_start, "--window-end", now_iso,
                 "--json"], ws)
            d = e.get("json") or {}
            st.note(recovery_assessment={k: d.get(k) for k in ("state", "sli", "eligible")})
            st.check(e["rc"] == 0, "recovery slo evaluate rc=0")
        dres = CLI.run(
            ["detect", "--service-id", ids["service_id"],
             "--window-start", rec_start, "--window-end", now_iso,
             "--now", now_iso, "--json"], ws)
        drep = dres.get("json") or {}
        danoms = drep.get("anomalies") or []
        fired = {
            (e.get("detector_id") or "")
            for e in (drep.get("evaluations") or [])
            if e.get("outcome") == "fired"
        }
        err_anoms = [x for x in danoms if "error_ratio" in str(fired)]
        st.check(not err_anoms,
                 f"no NEW error-ratio anomalies post-recovery ({len(danoms)} total)")
        st.note(post_recovery_anomalies=len(danoms))
        st.finish("pass" if st.ok else "fail", "LOCAL_REAL",
                  f"{share:.0%} ok, ingest accepted={ing.get('accepted')}")
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "LOCAL_REAL")


def s16_backup_restore(ws: Path) -> None:
    st = Step("S16", "backup create → restore → integrity + history parity")
    st.begin(command="backup create → backup restore → services/sources/incidents counts",
             provider="api-sentinel", workspace=str(ws))
    try:
        c = CLI.run(["backup", "create", "--json"], ws)
        bdir = ((c.get("json") or {}).get("backup_dir") or "")
        st.check(c["rc"] == 0 and bdir and Path(bdir).is_dir(),
                 f"backup created: {bdir}")
        # Never reuse a restore destination between runs.  Reusing the old
        # fixed directory made a valid restore refuse the live database and
        # compared the current workspace with stale rows from a prior run.
        # A fresh directory preserves the no-overwrite safety contract while
        # keeping the S16 evidence deterministic and isolated.
        ws2 = Path(tempfile.mkdtemp(prefix="ws-restored-", dir=RUN))
        r = CLI.run(["backup", "restore", "--from", bdir, "--json"], ws2)
        st.check(r["rc"] == 0 and (r.get("json") or {}).get("integrity") == "ok",
                 f"restore integrity={(r.get('json') or {}).get('integrity')}")
        backup_db = Path(bdir) / "sentinel.db"

        def db_snapshot(path: Path) -> dict[str, object]:
            conn = sqlite3.connect(str(path))
            try:
                integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
                fk = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
                tables = [
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    )
                ]
                rows: dict[str, object] = {}
                schema: dict[str, object] = {}
                for table in tables:
                    columns = [
                        tuple(row) for row in conn.execute(
                            f"PRAGMA table_info([{table}])"
                        )
                    ]
                    schema[table] = columns
                    if table == "audit_events":
                        continue  # backup/restore journals are legitimate deltas
                    rows[table] = sorted(
                        [tuple(row) for row in conn.execute(
                            f"SELECT * FROM [{table}]"  # noqa: S608
                        )], key=repr
                    )
                return {"integrity": integrity, "fk": fk,
                        "schema": schema, "rows": rows, "tables": tables}
            finally:
                conn.close()

        before = db_snapshot(backup_db)
        after = db_snapshot(ws2 / "sentinel.db")
        st.check(before["integrity"] == "ok", "backup database integrity=ok")
        st.check(after["integrity"] == "ok", "restored database integrity=ok")
        st.check(not before["fk"] and not after["fk"], "foreign keys valid")
        st.check(before["tables"] == after["tables"], "table set parity")
        st.check(before["schema"] == after["schema"], "table schema parity")
        differences = semantic_row_differences(before, after)
        before_rows = before["rows"]
        after_rows = after["rows"]
        for table in sorted(set(before_rows) | set(after_rows)):  # type: ignore[arg-type]
            st.check(table not in differences, f"semantic row parity {table}")

        def blob_hashes(root: Path) -> dict[str, str]:
            return {
                str(p.relative_to(root)).replace("\\", "/"): sha256(p)
                for p in sorted(root.rglob("*")) if p.is_file()
            } if root.is_dir() else {}

        expected_blobs = blob_hashes(Path(bdir) / "blobs")
        restored_blobs = blob_hashes(ws2 / "blobs")
        st.check(expected_blobs == restored_blobs, "blob set and hash parity")
        st.note(backup_dir=bdir, semantic_parity=True,
                table_count=len(before["tables"]),
                blob_count=len(expected_blobs),
                audit_delta="allowed: backup/restore append operational journal")
        st.finish("pass" if st.ok else "fail", "LOCAL_REAL",
                  "integrity + schema + rows + blobs parity")
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "LOCAL_REAL")


def s17_security(ws: Path, ids: dict[str, str]) -> Path:
    st = Step("S17", "§12 negative security battery")
    st.begin(command="lab/security_battery.py against the served API + workspace",
             provider="api-sentinel", workspace=str(ws))
    try:
        out_json = RUN / "security-results.json"
        b = run_cmd(
            [str(VENV_PY), str(ROOT / "lab" / "security_battery.py"),
             "--workspace", str(ws),
             "--service-id", ids["service_id"],
             "--source-id", ids["source_id"],
             "--cli", str(CLI.path),
             "--out", str(out_json)],
            timeout=900)
        st.note(battery_rc=b["rc"], tail=(b.get("stdout") or "")[-1500:],
                battery_err=(b.get("stderr") or "")[-600:])
        st.check(b["rc"] == 0, "battery ran to completion")
        if out_json.is_file():
            res = json.loads(out_json.read_text(encoding="utf-8"))
            total = res.get("summary", {})
            st.note(**total)
            breached = total.get("breached", 0)
            st.check(total.get("total", 0) > 30,
                     f"battery coverage total={total.get('total')}")
            st.check(breached == 0, f"zero control breaches ({breached})")
            st.artifact(out_json, "security-results.json")
        st.finish("pass" if st.ok else "fail",
                  "LOCAL_REAL", "see evidence/security-results.json")
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "LOCAL_REAL")
    return RUN / "security-results.json"


def _sanitize_bundle_value(value: object) -> object:
    """Remove host-specific paths and the lab token before hashing evidence."""
    if isinstance(value, dict):
        return {str(k): _sanitize_bundle_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_bundle_value(v) for v in value]
    if isinstance(value, str):
        result = value.replace(str(ROOT), "<checkout>")
        result = result.replace(str(Path.home()), "<home>")
        return result.replace(TOKEN, "<redacted-token>")
    return value


def _copy_bundle_inputs(bundle_root: Path) -> tuple[Path, Path]:
    """Copy a run's mutable files into its portable, self-contained bundle."""
    bundle_steps = bundle_root / "steps"
    bundle_artifacts = bundle_root / "artifacts"
    bundle_steps.mkdir(parents=True, exist_ok=True)
    bundle_artifacts.mkdir(parents=True, exist_ok=True)
    for source in sorted(STEPS_DIR.glob("S*.json")):
        (bundle_steps / source.name).write_text(
            json.dumps(_sanitize_bundle_value(json.loads(source.read_text(encoding="utf-8"))),
                       indent=2, default=str),
            encoding="utf-8",
        )
    for source in sorted(ART_DIR.rglob("*")):
        if not source.is_file():
            continue
        target = bundle_artifacts / source.relative_to(ART_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() == target.resolve():
            continue
        if source.suffix.lower() in {".json", ".jsonl", ".txt", ".log"}:
            with contextlib.suppress(UnicodeDecodeError):
                target.write_text(
                    str(_sanitize_bundle_value(source.read_text(encoding="utf-8"))),
                    encoding="utf-8",
                )
                continue
        shutil.copy2(source, target)
    return bundle_steps, bundle_artifacts


def _write_e18_evidence(
    record: dict[str, object],
    receipt_path: Path,
    manifest_path: Path,
    step_path: Path,
    aws_result: dict[str, object] | None,
) -> None:
    """Write the S18 artifact triad — receipt + step file + manifest.

    Order and content are deterministic: the S18 record carried inside the
    receipt is the *same* record written to ``steps/S18.json`` — the
    receipt is self-including, never missing its own step (C101-F002).
    The manifest hashes the final bytes of every evidence artifact.
    """
    bundle_root = step_path.parent.parent
    step_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_steps, bundle_artifacts = _copy_bundle_inputs(bundle_root)
    safe_oracle = [_sanitize_bundle_value(s) for s in ORACLE]
    safe_record = _sanitize_bundle_value(record)
    step_path.write_text(
        json.dumps(safe_record, indent=2, default=str), encoding="utf-8"
    )
    steps_all = [*safe_oracle, safe_record]
    summary = {
        "total": len(steps_all),
        "passed": sum(1 for s in steps_all if s["status"] == "pass"),
        "failed": sum(1 for s in steps_all if s["status"] == "fail"),
        "blocked": sum(1 for s in steps_all if s["status"] == "blocked"),
        "not_run": sum(1 for s in steps_all if s["status"] == "not_run"),
        "truth_labels": sorted({str(s["truth"]) for s in steps_all}),
    }
    receipt = {
        "schema": "sentinel-evidence/FederatedE2EReceipt/v1",
        "cycle": "10.3",
        "generated_at": utcnow(),
        "generator": "lab/federated_e2e.py",
        "commit": COMMIT,
        "steps": steps_all,
        "summary": summary,
    }
    if aws_result is not None:
        receipt["aws_validation"] = aws_result
    receipt_path.write_text(
        json.dumps(receipt, indent=2, default=str), encoding="utf-8"
    )
    runtime_path = bundle_root / "environment" / "runtime.json"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(json.dumps({
        "schema": "api-sentinel/Cycle103Runtime/v1",
        "run_id": bundle_root.name,
        "commit": COMMIT,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "profile": "federated-local",
        "truth": "LOCAL_REAL",
    }, indent=2), encoding="utf-8")
    bundle_meta = bundle_root / "bundle.json"
    bundle_meta.write_text(json.dumps({
        "schema": "api-sentinel/EvidenceBundle/v1",
        "cycle": "10.3",
        "run_id": bundle_root.name,
        "commit": COMMIT,
        "receipt": "federated-e2e-receipt.json",
        "manifest": "federated-e2e-manifest.json",
    }, indent=2), encoding="utf-8")
    files = []
    for p in sorted(bundle_steps.glob("S*.json")):
        files.append({
            "path": str(p.relative_to(bundle_root)).replace("\\", "/"),
            "sha256": sha256(p),
            "bytes": p.stat().st_size,
        })
    for p in [*sorted(bundle_artifacts.rglob("*")), runtime_path,
              bundle_meta, receipt_path]:
        if p.is_file():
            files.append({
                "path": str(p.relative_to(bundle_root)).replace("\\", "/"),
                "sha256": sha256(p),
                "bytes": p.stat().st_size,
            })
    manifest = {
        "schema": "api-sentinel/EvidenceManifest/v1",
        "cycle": "cycle-10.3",
        "generated_at": utcnow(),
        "generator": "lab/federated_e2e.py",
        "commit": COMMIT,
        "files": files,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )


def s18_receipt(ws: Path, aws_result: dict[str, object] | None = None) -> Path:
    st = Step("S18", "consolidated federated receipt + evidence manifest "
                     "+ independent verification")
    st.begin(command="assemble self-including receipt + S1-S18 step files "
                     "+ hash manifest + verify_evidence.py",
             provider="api-sentinel", workspace=str(ws))
    bundle_root = BUNDLE_ROOT or ROOT
    if bundle_root == ROOT:
        receipt_path = EVIDENCE / "federated-e2e-receipt.json"
        manifest_path = EVIDENCE / "federated-e2e-manifest.json"
        step_path = STEPS_DIR / "S18.json"
        verify_root = ROOT
    else:
        receipt_path = bundle_root / "federated-e2e-receipt.json"
        manifest_path = bundle_root / "federated-e2e-manifest.json"
        step_path = bundle_root / "steps" / "S18.json"
        verify_root = bundle_root
    try:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        st.record["artifacts"] = [
            str(receipt_path), str(manifest_path), str(step_path),
        ]
        st.record["truth"] = "LOCAL_REAL"
        # S18 is self-including.  Seed it with a valid terminal status before
        # the first verification; a transient ``running`` status would make
        # an otherwise valid bundle fail the independent verifier.
        st.status = "pass"
        st.record["status"] = "pass"
        st.record["output"]["asserts"] = [{
            "check": "receipt embeds every step including S18 itself",
            "ok": True,
        }]
        st.record["finished_at"] = utcnow()
        _write_e18_evidence(
            st.record, receipt_path, manifest_path, step_path, aws_result
        )
        embedded = len(json.loads(
            receipt_path.read_text(encoding="utf-8")
        )["steps"]) == len(ORACLE) + 1
        st.record["output"]["asserts"][0]["ok"] = embedded
        if not embedded:
            st.ok = False
            st.record["errors"].append(
                "assert failed: receipt embeds every step including S18 itself"
            )
        # Independent verification — a second process recomputes every
        # hash and cross-checks receipt ↔ manifest ↔ step files.
        v = run_cmd(
            [sys.executable, str(ROOT / "lab" / "verify_evidence.py"),
             "--receipt", str(receipt_path),
             "--manifest", str(manifest_path),
             "--root", str(verify_root)],
            timeout=120,
        )
        st.note(verifier_rc=v["rc"],
                verifier_tail=(v.get("stdout") or "")[-800:])
        st.record["output"]["asserts"].append({
            "check": "independent manifest verification agrees",
            "ok": v["rc"] == 0,
        })
        if v["rc"] != 0:
            st.ok = False
            st.record["errors"].append(
                "assert failed: independent manifest verification agrees"
            )
        st.status = "pass" if st.ok else "fail"
        st.record["status"] = st.status
        _write_e18_evidence(
            st.record, receipt_path, manifest_path, step_path, aws_result,
        )
        ORACLE.append(st.record)
        mark = {"pass": "PASS", "fail": "FAIL"}.get(st.status, st.status)
        print(f"[S18  {mark:<5} LOCAL_REAL     ] {st.name}")
    except Exception as exc:
        st.record["errors"].append(f"{type(exc).__name__}: {exc}")
        st.finish("fail", "LOCAL_REAL")
    return receipt_path


def _kill_all() -> None:
    for p in PROCS:
        with contextlib.suppress(Exception):
            p.kill()
    # verified cleanup: ports must free
    for port in (DEP_PORT, DEMO_PORT, SENTINEL_PORT):
        wait_closed(port)


def main() -> int:
    global ART_DIR, BUNDLE_ROOT, RUN, SKIP_TRAFFIC_SLEEP, STEPS_DIR

    # argparse renders the module docstring for --help.  Configure the
    # console before parsing so Windows cp1252 cannot make help itself fail.
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traffic-seconds", type=float, default=66.0,
                        help="per-phase burst duration (needs ≥1 closed 60s window)")
    parser.add_argument("--steps", type=str, default="",
                        help="CSV subset of step ids to run (e.g. S2,S3)")
    parser.add_argument(
        "--skip-traffic-sleep", action="store_true",
        help="diagnostic mode: do not wait for a closed 60s export window",
    )
    args = parser.parse_args()

    SKIP_TRAFFIC_SLEEP = bool(args.skip_traffic_sleep)
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    RUN = RUN_BASE / run_id
    BUNDLE_ROOT = EVIDENCE / "bundles" / run_id
    STEPS_DIR = RUN / "steps"
    ART_DIR = RUN / "artifacts"
    ORACLE.clear()
    PROCS.clear()
    RUN.mkdir(parents=True, exist_ok=True)
    ART_DIR.mkdir(parents=True, exist_ok=True)
    STEPS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"federated_e2e — run root: {RUN}\ncommit: {COMMIT}\nrun_id: {run_id}")

    proj = RUN / "proj"
    ws = proj / ".api-sentinel"
    demo = f"http://127.0.0.1:{DEMO_PORT}"
    dep = f"http://127.0.0.1:{DEP_PORT}"
    only = {s.strip() for s in args.steps.split(",") if s.strip()}

    boot_py = VENV_PY
    ids: dict[str, str] = {}
    inv: dict[str, object] = {}
    slo: dict[str, object] = {}
    try:
        if not only or "S1" in only:
            boot_py = s1_bootstrap()
        else:
            boot_py = resolve_python(ROOT, venv=RUN / "boot-venv")
            CLI.path = resolve_entrypoint("api-sentinel", python=boot_py)
        if not only or "S2" in only:
            s2_demo_start(boot_py)
        ids = (
            s3_register(proj)
            if not only or "S3" in only
            else _recover_ids(ws)
        )
        if not only or "S4" in only:
            s4_traffic(demo, args.traffic_seconds)
        if not only or "S5" in only:
            s5_ingest(demo, ws, ids)
        if not only or "S6" in only:
            slo = s6_slo(ws, ids)
        if not only or "S7" in only:
            s7_degrade(dep, demo, ws, ids, args.traffic_seconds)
        if not only or "S8" in only:
            inv = s8_detect_investigate(ws, ids)
        if not only or "S9" in only:
            s9_doctor(ws, ids)
        if not only or "S10" in only:
            s10_atlas(ws, ids, inv.get("incident_id") or _first_incident(ws))
        if not only or "S11" in only:
            s11_theforge(proj)
        if not only or "S12" in only:
            s12_bridge(ws, inv)
        if not only or "S13" in only:
            s13_lens(ws)
        if not only or "S14" in only:
            s14_portal(ws, ids)
        if not only or "S15" in only:
            s15_recovery(dep, demo, ws, ids, args.traffic_seconds, slo)
        if not only or "S16" in only:
            s16_backup_restore(ws)
        if not only or "S17" in only:
            s17_security(ws, ids)
        if not only or "S18" in only:
            s18_receipt(ws)
    finally:
        _kill_all()
        print("\ncleanup: demo/dep/serve processes terminated; "
              f"ports free: dep={port_free(DEP_PORT)} "
              f"demo={port_free(DEMO_PORT)} sentinel={port_free(SENTINEL_PORT)}")

    fails = [s for s in ORACLE if s["status"] == "fail"]
    print(f"\nverdict: {'FAIL' if fails else 'PASS'} "
          f"({sum(1 for s in ORACLE if s['status']=='pass')}/{len(ORACLE)} pass, "
          f"{sum(1 for s in ORACLE if s['status']=='blocked')} blocked)")
    return 1 if fails else 0


def _recover_ids(ws: Path) -> dict[str, str]:
    """When steps run selectively, recover svc/src ids from the workspace."""
    try:
        svcs = CLI.run(["services", "--json"], ws).get("json") or []
        srcs = CLI.run(["sources", "--json"], ws).get("json") or []
        return {
            "service_id": svcs[0]["service_id"] if svcs else "",
            "source_id": srcs[0]["source_id"] if srcs else "",
        }
    except Exception:
        return {}


def _first_incident(ws: Path) -> str:
    try:
        incs = CLI.run(["incidents", "--json"], ws).get("json") or []
        return str(incs[0]["incident_id"]) if incs else ""
    except Exception:
        return ""


if __name__ == "__main__":
    sys.exit(main())
