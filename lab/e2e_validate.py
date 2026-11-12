"""End-to-end validation runner for api-sentinel-forge (final pass).

Drives the REAL ``api-sentinel`` CLI (subprocess) plus httpx against the
served ``/v1`` surface through a full lab pipeline:

install -> services -> sources -> ingest -> slo -> detect -> incidents ->
investigate -> changes correlate -> hypotheses verify/promote ->
handoff doctor -> serve (HTTP) -> backup/restore -> retention -> doctor ->
restart-recovery -> capabilities.

Determinism: every detection/evaluation call pins ``--now`` /
``--window-*`` to the fixture clock (2026-01-01); no wall-clock logic.

Fixture id remap (documented in lab READMEs — "rename service_id/source
to your registered id"): the seed ids ``svc_lab_payments`` /
``svc_lab_checkout`` / ``src_lab_jsonl`` are replaced in temp copies by
the generated ULIDs. ``lab-002`` (persistent 503) is mapped onto
``orders-api`` so ``checkout-api`` stays a clean regressive-deploy
narrative — its fixture service id is arbitrary seed data, never a name.

No mocks; no product code is touched. Exit 0 only if every step passes.
"""

# ruff: noqa: T201 S603 -- lab runner: stdout IS the report; subprocess is the
# point (it must exercise the real installed CLI, never import it).

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / ".venv" / "Scripts" / "api-sentinel.exe"
FIXTURES = ROOT / "lab" / "fixtures"

# Deterministic fixture clock — every --now/--window-* pins here.
NOW = "2026-01-01T00:12:00Z"
T0 = "2026-01-01T00:00:00Z"

SECRET_PATTERNS = [
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?i)aws_secret_access_key",
    r"(?i)secret_access_key",
    r"(?i)password\s*[:=]",
    r"(?i)bearer\s+[a-z0-9._~+/=-]{8,}",
    r"(?i)api[_-]?key\s*[:=]\s*['\"]?[a-z0-9]{12,}",
    r"(?i)token\s*[:=]\s*['\"]?[a-z0-9]{12,}",
]


@dataclass
class Step:
    """One scenario step — commands run, real output, verdict."""

    name: str
    status: str = "PASS"
    lines: list[str] = field(default_factory=list)

    def emit(self, line: str = "") -> None:
        self.lines.append(line)
        print(f"  {line}")

    def check(self, cond: bool, label: str) -> bool:
        if cond:
            self.emit(f"assert ok: {label}")
            return True
        self.status = "FAIL"
        self.emit(f"ASSERT-FAIL: {label}")
        return False


STEPS: list[Step] = []
TRANSCRIPT: dict[str, object] = {"steps": STEPS}


def _env() -> dict[str, str]:
    """Child env — UTF-8 forced so rich output never hits the cp1252
    console-encoding crash observed on legacy Windows consoles."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    return env


@dataclass
class Cmd:
    rc: int
    out: str
    err: str

    def json(self) -> object | None:
        try:
            return json.loads(self.out)
        except (json.JSONDecodeError, ValueError):
            return None


def run(args: list[str], ws: Path | None = None, timeout: int = 120) -> Cmd:
    cmd = [str(CLI), *args]
    if ws is not None:
        cmd += ["--workspace", str(ws)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=_env(), timeout=timeout)
    return Cmd(proc.returncode, proc.stdout.strip(), proc.stderr.strip())


def show(step: Step, cmd: Cmd, args_desc: str, *, head: int = 1) -> bool:
    """Record a command + its real (trimmed) output; return rc==0."""
    step.emit(f"$ api-sentinel {args_desc}   [rc={cmd.rc}]")
    payload = cmd.out or cmd.err or "(no output)"
    step.emit(f"-> {payload[:600]}")
    if cmd.rc != 0:
        step.status = "FAIL"
        step.emit(f"non-zero exit ({cmd.rc}): {cmd.err[:400]}")
    return cmd.rc == 0


def remap(fixture: Path, tmp: Path, mapping: dict[str, str]) -> Path:
    text = fixture.read_text(encoding="utf-8")
    for old, new in mapping.items():
        text = text.replace(old, new)
    out = tmp / fixture.name
    out.write_text(text, encoding="utf-8")
    return out


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> int:
    if not CLI.exists():
        print(f"FATAL: CLI not found: {CLI}")
        return 1
    print(f"repo: {ROOT}")
    print(f"cli:  {CLI}")
    work = Path(tempfile.mkdtemp(prefix="sentinel-e2e-"))
    ws = work / "ws"
    ws2 = work / "ws-restored"
    tmp = work / "fixtures"
    tmp.mkdir(parents=True)
    print(f"temp workspace root: {ws}")
    print(f"temp restore  root: {ws2}\n")

    # ------------------------------------------------------------------
    st = Step("S1 install --yes --workspace WS --json")
    STEPS.append(st)
    print("== S1 install ==")
    show(st, run(["install", "--yes", "--json"], ws), "install --yes --workspace WS --json")
    st.check(ws.is_dir() and (ws / "sentinel.db").exists(), "workspace root + sentinel.db created")

    # ------------------------------------------------------------------
    st = Step("S2 register three services")
    STEPS.append(st)
    print("\n== S2 services --register ==")
    svc: dict[str, str] = {}
    for name in ("payments-api", "orders-api", "checkout-api"):
        c = run(["services", "--register", name, "--json"], ws)
        show(st, c, f"services --register {name} --workspace WS --json")
        data = c.json()
        if isinstance(data, dict) and data.get("service_id"):
            svc[name] = str(data["service_id"])
        else:
            st.status = "FAIL"
            st.emit("ASSERT-FAIL: no service_id in output")
    st.check(len(svc) == 3 and len(set(svc.values())) == 3, "3 distinct service ids")
    for name, sid in svc.items():
        st.emit(f"service: {name} -> {sid}")

    # ------------------------------------------------------------------
    st = Step("S3 register jsonl-file sources")
    STEPS.append(st)
    print("\n== S3 sources --register jsonl-file ==")
    src: dict[str, str] = {}
    for name in ("payments-api", "orders-api", "checkout-api"):
        if name not in svc:
            continue
        c = run(["sources", "--register", "jsonl-file", "--service-id", svc[name], "--json"], ws)
        show(st, c, f"sources --register jsonl-file --service-id {svc[name]} --json")
        data = c.json()
        if isinstance(data, dict) and data.get("source_id"):
            src[name] = str(data["source_id"])
        else:
            st.status = "FAIL"
            st.emit("ASSERT-FAIL: no source_id in output")
    st.check(len(src) == 3, "3 source ids captured")

    # ------------------------------------------------------------------
    st = Step("S4 ingest lab fixtures (id-remapped) + deploy manifest")
    STEPS.append(st)
    print("\n== S4 ingest + changes import ==")
    lab001 = remap(
        FIXTURES / "lab-001-elevated-latency.jsonl",
        tmp,
        {"svc_lab_payments": svc["payments-api"], "src_lab_jsonl": src["payments-api"]},
    )
    lab002 = remap(
        FIXTURES / "lab-002-persistent-503.jsonl",
        tmp,
        {"svc_lab_checkout": svc["orders-api"], "src_lab_jsonl": src["orders-api"]},
    )
    lab004_dir = FIXTURES / "lab-004-regressive-deploy"
    st.emit(f"dir listing lab-004: {sorted(p.name for p in lab004_dir.iterdir())}")
    lab004 = remap(
        lab004_dir / "observations.jsonl",
        tmp,
        {"svc_lab_checkout": svc["checkout-api"], "src_lab_jsonl": src["checkout-api"]},
    )
    for label, sid, f, want in (
        ("lab-001->payments", src["payments-api"], lab001, 8),
        ("lab-002->orders", src["orders-api"], lab002, 16),
        ("lab-004->checkout", src["checkout-api"], lab004, 18),
    ):
        c = run(["ingest", "--source-id", sid, "--file", str(f), "--json"], ws)
        show(st, c, f"ingest --source-id {sid} --file {f.name} --json")
        d = c.json() or {}
        st.check(
            d.get("accepted") == want and d.get("quarantined") == 0,
            f"{label}: accepted={want} quarantined=0 (got {d.get('accepted')}/"
            f"{d.get('quarantined')})",
        )
    # idempotent re-ingest of the same bytes -> whole-batch dedup
    c = run(["ingest", "--source-id", src["payments-api"], "--file", str(lab001), "--json"], ws)
    show(st, c, "ingest lab-001 again --json")
    d = c.json() or {}
    st.check(
        d.get("accepted") == 0 and d.get("skipped_duplicates") == 8,
        f"re-ingest dedup: skipped_duplicates=8 (got {d.get('skipped_duplicates')})",
    )
    manifest = lab004_dir / "deploy-manifest.json"
    if manifest.exists():
        c = run(["changes", "import", "--file", str(manifest), "--json"], ws)
        show(st, c, "changes import --file deploy-manifest.json --json")
        d = c.json() or {}
        st.check(
            d.get("imported") == 2 and d.get("skipped") == 0,
            f"manifest imported=2 skipped=0 (got {d.get('imported')}/{d.get('skipped')})",
        )
    else:
        st.emit("deploy-manifest.json absent — skipped honestly")

    # ------------------------------------------------------------------
    st = Step("S5 slo define + evaluate")
    STEPS.append(st)
    print("\n== S5 slo ==")
    slo_ids: dict[str, str] = {}
    for name in ("payments-api", "orders-api"):
        c = run(
            [
                "slo",
                "define",
                "--service-id",
                svc[name],
                "--target",
                "0.99",
                "--window",
                "1h",
                "--json",
            ],
            ws,
        )
        show(st, c, f"slo define --service-id {svc[name]} --target 0.99 --window 1h --json")
        d = c.json() or {}
        if d.get("slo_id"):
            slo_ids[name] = str(d["slo_id"])
        else:
            st.status = "FAIL"
            st.emit("ASSERT-FAIL: no slo_id")
    for name, (a, b) in (
        ("payments-api", (T0, "2026-01-01T01:00:00Z")),
        ("orders-api", (T0, "2026-01-01T00:10:00Z")),
    ):
        if name not in slo_ids:
            continue
        c = run(
            [
                "slo",
                "evaluate",
                "--slo-id",
                slo_ids[name],
                "--window-start",
                a,
                "--window-end",
                b,
                "--json",
            ],
            ws,
        )
        show(st, c, f"slo evaluate --slo-id {slo_ids[name]} {a}..{b} --json")
        d = c.json() or {}
        st.emit(
            f"assessment[{name}]: state={d.get('state')} "
            f"sli={d.get('sli')} eligible={d.get('eligible')}"
        )

    # ------------------------------------------------------------------
    st = Step("S6 detect (latency+error) + dedup re-run")
    STEPS.append(st)
    print("\n== S6 detect ==")
    windows = {
        "payments-api": (T0, "2026-01-01T00:08:00Z"),
        "orders-api": (T0, "2026-01-01T00:10:00Z"),
        "checkout-api": ("2026-01-01T00:04:00Z", "2026-01-01T00:10:00Z"),
    }
    for name, (a, b) in windows.items():
        args = [
            "detect",
            "--service-id",
            svc[name],
            "--window-start",
            a,
            "--window-end",
            b,
            "--now",
            NOW,
            "--json",
        ]
        c = run(args, ws)
        show(st, c, f"detect --service-id {svc[name]} {a}..{b} --now {NOW} --json")
        d = c.json() or {}
        anoms = d.get("anomalies") or []
        kinds = sorted({x.get("rule", {}).get("detector_id") for x in anoms})
        st.emit(f"anomalies[{name}]: {len(anoms)} -> {kinds}")
        st.check(len(anoms) >= 1, f"{name}: >=1 anomaly")
        # dedup: identical re-run must produce zero new anomalies
        c2 = run(args, ws)
        d2 = c2.json() or {}
        st.check(
            c2.rc == 0 and not (d2.get("anomalies") or []) and bool(d2.get("deduplicated")),
            f"{name}: re-detect 0 new anomalies, deduplicated={d2.get('deduplicated')}",
        )

    # ------------------------------------------------------------------
    st = Step("S7 incidents list + ack + show")
    STEPS.append(st)
    print("\n== S7 incidents ==")
    c = run(["incidents", "--json"], ws)
    show(st, c, "incidents --workspace WS --json")
    incs = c.json() if isinstance(c.json(), list) else []
    st.check(len(incs) >= 1, f"incidents exist ({len(incs)})")
    co_inc = next(
        (
            i
            for i in incs
            if i.get("service_id") == svc["checkout-api"]
            and "error_ratio" in str(i.get("group_key", ""))
        ),
        None,
    )
    inc_id = str(co_inc["incident_id"]) if co_inc else ""
    st.check(bool(inc_id), "checkout error_ratio incident found")
    st.emit(
        f"checkout incident: {inc_id} "
        f"first_observed_at={co_inc.get('first_observed_at') if co_inc else '?'}"
    )
    if inc_id:
        c = run(["incident", "ack", inc_id, "--actor", "e2e", "--note", "e2e triage", "--json"], ws)
        show(st, c, f"incident ack {inc_id} --json")
        st.check((c.json() or {}).get("status") == "TRIAGED", "ack -> TRIAGED")
        c = run(["incident", "show", inc_id, "--json"], ws)
        show(st, c, f"incident show {inc_id} --json")
        d = c.json() or {}
        st.check((d.get("incident") or {}).get("status") == "TRIAGED", "show: status TRIAGED")
        st.check(
            len(d.get("events") or []) >= 2,
            f"show: journal events present ({len(d.get('events') or [])})",
        )

    # ------------------------------------------------------------------
    st = Step("S8 investigate --incident-id")
    STEPS.append(st)
    print("\n== S8 investigate ==")
    if inc_id:
        c = run(
            [
                "investigate",
                "--incident-id",
                inc_id,
                "--provider",
                "claude",
                "--cost-limit",
                "2.0",
                "--json",
            ],
            ws,
        )
        show(st, c, f"investigate --incident-id {inc_id} --provider claude --json")
        d = c.json() or {}
        sess = d.get("session") or {}
        st.emit(f"session.status={sess.get('status')} detail={sess.get('detail')!r}")
        st.check(bool(d.get("pack")), "bounded context pack emitted")
        st.check(
            sess.get("status") in ("delegated", "unavailable", "refused"),
            f"honest session status (got {sess.get('status')!r})",
        )
        envelopes = list((ws / "agent_sessions").glob("**/*"))
        envelopes = [p for p in envelopes if p.is_file()]
        st.emit(f"agent_sessions files: {len(envelopes)}")
    else:
        st.status = "FAIL"
        st.emit("BLOCKED: no incident id from S7")

    # ------------------------------------------------------------------
    st = Step("S9 changes correlate + hypotheses add/verify/promote")
    STEPS.append(st)
    print("\n== S9 correlate + hypotheses ==")
    change_id = hyp_id = ver_id = ""
    if inc_id:
        c = run(["changes", "correlate", "--incident-id", inc_id, "--json"], ws)
        show(st, c, f"changes correlate --incident-id {inc_id} --json")
        d = c.json() or {}
        cands = d.get("candidates") or []
        st.emit(f"anchor={d.get('anchor')} candidates={len(cands)} unknowns={d.get('unknowns')}")
        if cands:
            top = cands[0]
            ch = top.get("change") or {}
            change_id = str(ch.get("change_id") or "")
            st.check(
                ch.get("kind") == "deploy" and abs(top.get("delta_seconds", -1) - 30) < 1,
                f"deploy ranked first delta~30s (got kind={ch.get('kind')} "
                f"delta={top.get('delta_seconds')})",
            )
            st.check(bool(d.get("proximity_ne_cause")), "proximity!=cause disclaimer present")
        else:
            st.check(False, "correlate produced no candidates")
    if inc_id and change_id:
        c = run(
            [
                "hypotheses",
                "add",
                "--incident-id",
                inc_id,
                "--text",
                "deploy sha_a11ce99 (v2.7.0) preceded the 503 regression",
                "--confidence",
                "CORRELATED",
                "--evidence-for",
                change_id,
                "--json",
            ],
            ws,
        )
        show(st, c, "hypotheses add --confidence CORRELATED --json")
        d = c.json() or {}
        hyp_id = str(d.get("hypothesis_id") or "")
        st.check(
            bool(hyp_id) and d.get("confidence") == "CORRELATED",
            f"hypothesis CORRELATED (got {d.get('confidence')})",
        )
    if inc_id and hyp_id:
        c = run(
            [
                "hypotheses",
                "verify",
                "--incident-id",
                inc_id,
                "--hypothesis-id",
                hyp_id,
                "--window-start",
                "2026-01-01T00:10:00Z",
                "--window-end",
                "2026-01-01T00:12:00Z",
                "--json",
            ],
            ws,
        )
        show(st, c, "hypotheses verify --window 00:10..00:12 --json")
        d = c.json() or {}
        ver_id = str(d.get("verification_id") or "")
        st.emit(
            f"verdict={d.get('verdict')} receipts="
            f"{[(r.get('step_id'), r.get('status')) for r in d.get('receipts') or []]}"
        )
        st.check(
            d.get("verdict") == "VERIFIED" and bool(ver_id),
            f"verify -> VERIFIED (got {d.get('verdict')})",
        )
    if inc_id and hyp_id and ver_id:
        c = run(
            [
                "hypotheses",
                "promote",
                "--incident-id",
                inc_id,
                "--hypothesis-id",
                hyp_id,
                "--verification-id",
                ver_id,
                "--json",
            ],
            ws,
        )
        show(st, c, "hypotheses promote --verification-id --json")
        d = c.json() or {}
        st.check(
            d.get("confidence") == "VERIFIED", f"promote -> VERIFIED (got {d.get('confidence')})"
        )

    # ------------------------------------------------------------------
    st = Step("S10 handoff doctor + receipt secret-scan")
    STEPS.append(st)
    print("\n== S10 handoff doctor ==")
    c = run(["handoff", "doctor", "--service-id", svc["checkout-api"], "--json"], ws)
    show(st, c, f"handoff doctor --service-id {svc['checkout-api']} --json")
    d = c.json() or {}
    st.emit(
        f"handoff status={d.get('status')} mode={d.get('mode')} request_ref={d.get('request_ref')}"
    )
    docs = [p for sub in ("handoffs", "receipts") for p in (ws / sub).glob("**/*") if p.is_file()]
    st.check(len(docs) >= 1, f"handoff/receipt files under workspace ({len(docs)})")
    leaks: list[str] = []
    for p in docs:
        text = p.read_text(encoding="utf-8", errors="replace")
        for pat in SECRET_PATTERNS:
            if re.search(pat, text):
                leaks.append(f"{p.name}:{pat}")
    st.check(not leaks, f"no secret patterns in {len(docs)} handoff/receipt files")
    if leaks:
        st.emit(f"leaks: {leaks}")

    # ------------------------------------------------------------------
    st = Step("S11 serve + httpx /v1 surface (token auth)")
    STEPS.append(st)
    print("\n== S11 serve ==")
    port = free_port()
    # Cycle-10 auth: a fixed env token keeps it out of the transcript —
    # the generated-token path prints the secret once on stderr.
    lab_token = "lab-e2e-token-" + "0" * 32
    serve_env = _env()
    serve_env["API_SENTINEL_TOKEN"] = lab_token
    proc = subprocess.Popen(
        [str(CLI), "serve", "--host", "127.0.0.1", "--port", str(port), "--workspace", str(ws)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=serve_env,
    )
    base = f"http://127.0.0.1:{port}"
    authz = {"Authorization": f"Bearer {lab_token}"}
    up = False
    try:
        with httpx.Client(base_url=base, timeout=5.0) as http:
            for _ in range(80):
                if proc.poll() is not None:
                    break
                try:
                    if http.get("/v1/health").status_code == 200:
                        up = True
                        break
                except httpx.TransportError:
                    time.sleep(0.25)
            st.check(up, f"server up on {base}")
            if up:
                # unauthenticated reads must refuse — the C10-F007 contract
                r = http.get("/v1/services")
                st.emit(f"GET /v1/services (no token) -> {r.status_code}")
                st.check(r.status_code == 401, "no token -> 401")
                st.check(
                    r.headers.get("www-authenticate", "").startswith("Bearer"),
                    "401 carries WWW-Authenticate: Bearer",
                )
                r = http.get("/v1/services", headers={"X-Sentinel-Role": "admin"})
                st.emit(f"GET /v1/services (self-declared admin) -> {r.status_code}")
                st.check(r.status_code == 401, "role spoof without credentials -> 401")
                for path in (
                    "/v1/health",
                    "/v1/capabilities",
                    "/v1/services",
                    f"/v1/services/{svc['payments-api']}",
                    "/v1/incidents",
                    f"/v1/incidents/{inc_id}/timeline",
                    "/v1/integrations/status",
                ):
                    r = http.get(path, headers=authz)
                    st.emit(
                        f"GET {path} -> {r.status_code} "
                        f"{r.headers.get('content-type', '')} ({len(r.content)}B)"
                    )
                    st.check(r.status_code == 200, f"GET {path} == 200")
                r = http.post(
                    f"/v1/incidents/{inc_id}/investigations",
                    json={"idempotency_key": "e2e-inv-1", "note": "e2e http"},
                    headers=authz,
                )
                st.emit(f"POST /v1/incidents/{inc_id}/investigations -> {r.status_code}")
                st.check(r.status_code in (200, 201), f"POST investigations 2xx ({r.status_code})")
                r2 = http.post(
                    f"/v1/incidents/{inc_id}/investigations",
                    json={"idempotency_key": "e2e-inv-1"},
                    headers=authz,
                )
                st.emit(
                    f"POST replay -> {r2.status_code} replayed="
                    f"{(r2.json() or {}).get('replayed') if r2.content else '?'}"
                )
                st.check(r2.status_code in (200, 201), "idempotent replay accepted")
                r3 = http.post(f"/v1/incidents/{inc_id}/execute", json={}, headers=authz)
                ctype = r3.headers.get("content-type", "")
                st.emit(f"POST /v1/incidents/{inc_id}/execute -> {r3.status_code} {ctype}")
                st.check(r3.status_code == 404, "POST /execute -> 404")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        srv_log = proc.stdout.read() if proc.stdout else ""
        st.emit(f"server rc={proc.returncode}; log tail: {srv_log[-200:]!r}")

    # ------------------------------------------------------------------
    st = Step("S12 backup create + restore + count parity")
    STEPS.append(st)
    print("\n== S12 backup/restore ==")
    c = run(["backup", "create", "--json"], ws)
    show(st, c, "backup create --workspace WS --json")
    bdir = (c.json() or {}).get("backup_dir") if c.rc == 0 else None
    st.check(bool(bdir) and Path(str(bdir)).is_dir(), f"backup dir exists ({bdir})")
    n_svc = len(run(["services", "--json"], ws).json() or [])
    n_src = len(run(["sources", "--json"], ws).json() or [])
    n_inc = len(run(["incidents", "--json"], ws).json() or [])
    if bdir:
        c = run(["backup", "restore", "--from", str(bdir), "--json"], ws2)
        show(st, c, f"backup restore --from {bdir} --workspace WS2 --json")
        st.check(
            c.rc == 0 and (c.json() or {}).get("integrity") == "ok",
            f"restore integrity=ok (got {(c.json() or {}).get('integrity')})",
        )
        r_svc = len(run(["services", "--json"], ws2).json() or [])
        r_src = len(run(["sources", "--json"], ws2).json() or [])
        r_inc = len(run(["incidents", "--json"], ws2).json() or [])
        st.emit(f"counts primary: services={n_svc} sources={n_src} incidents={n_inc}")
        st.emit(f"counts restored: services={r_svc} sources={r_src} incidents={r_inc}")
        st.check((r_svc, r_src, r_inc) == (n_svc, n_src, n_inc), "restored counts identical")

    # ------------------------------------------------------------------
    st = Step("S13 retention dry-run + doctor self-observability")
    STEPS.append(st)
    print("\n== S13 retention + doctor ==")
    c = run(["retention", "--json"], ws)
    show(st, c, "retention --workspace WS --json (dry-run)")
    d = c.json() or {}
    st.check(c.rc == 0 and isinstance(d, dict) and bool(d), "retention report emitted")
    st.emit(f"retention keys: {sorted(d)[:10]}")
    c = run(["doctor", "--json"], ws)
    show(st, c, "doctor --workspace WS --json")
    d = c.json() or {}
    block = d.get("sentinel") or {}
    st.check(
        bool(block.get("state")),
        f"sentinel self-observability present (state={block.get('state')})",
    )

    # ------------------------------------------------------------------
    st = Step("S14 restart-recovery (fresh process, same workspace)")
    STEPS.append(st)
    print("\n== S14 restart recovery ==")
    c = run(["services", "--json"], ws)
    ids = sorted(s.get("service_id") for s in (c.json() or []))
    st.check(
        c.rc == 0 and ids == sorted(svc.values()),
        "services --json returns the same 3 services after restart",
    )

    # ------------------------------------------------------------------
    st = Step("S15 capabilities honest states")
    STEPS.append(st)
    print("\n== S15 capabilities ==")
    c = run(["capabilities", "--json"])
    show(st, c, "capabilities --json")
    caps = c.json() if isinstance(c.json(), list) else []
    honest = {cap["capability_id"]: cap["status"] for cap in caps}
    for k, v in honest.items():
        st.emit(f"capability: {k} = {v}")
    st.check(
        all(v in ("available", "declared", "unavailable") for v in honest.values()),
        "all capability states in {available,declared,unavailable}",
    )
    st.check(
        honest.get("api.agentic-investigation") in ("declared", "unavailable"),
        f"agentic-investigation honestly non-available "
        f"(got {honest.get('api.agentic-investigation')})",
    )

    # ------------------------------------------------------------------
    print("\n===== SUMMARY =====")
    fails = [s for s in STEPS if s.status != "PASS"]
    for s in STEPS:
        print(f"{s.status:>5}  {s.name}")
    print(
        f"\nverdict: {'PASS' if not fails else 'FAIL'} "
        f"({len(STEPS) - len(fails)}/{len(STEPS)} steps)"
    )
    transcript = work / "e2e-transcript.txt"
    transcript.write_text(
        "\n".join(f"== {s.name} [{s.status}]\n" + "\n".join(s.lines) for s in STEPS),
        encoding="utf-8",
    )
    print(f"transcript: {transcript}")
    print(f"work dir preserved for inspection: {work}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
