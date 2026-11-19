"""Install-2.1 engine for the service-forge install (Cycle 16 wave 11).

Three honest layers — the same split every sibling forge keeps:

- ``~/.forge/installations/api-sentinel-forge.json`` — account manifest
  (``forge/InstallationManifest/v1``). ``scripts/forge_bootstrap.py``
  writes it on the canonical wrapper route; ``api-sentinel install``
  merge-updates it so a CLI-only install is still discoverable.
- ``<target>/.forge/context/`` — workspace context registry, owned by the
  vendored ``_forgecontext`` transactions driven from ``cli/install.py``.
- ``<state>/install/receipts/<op>-<ts>.json`` — this forge's
  ``forge/InstallReceipt/v1`` records. The orchestrator globs the nested
  ``.<state>/install/receipts`` convention (plus ``.<state>/receipts``);
  domain handoff/retention receipts stay under ``.<state>/receipts`` and
  never mix with install receipts.

Honesty rules kept from the invariants: a check reports exactly what ran
(PASS/FAIL/BLOCKED/UNVERIFIED/NOT_APPLICABLE), ``verification.status`` is
PASS only when every check is PASS or NOT_APPLICABLE, and nothing here
ever writes a credential into a receipt — the token file's *path* is
metadata, the token itself never is.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api_sentinel import __version__
from api_sentinel.workspace import Workspace

SCHEMA_RECEIPT = "forge/InstallReceipt/v1"
SCHEMA_MANIFEST = "forge/InstallationManifest/v1"
SCHEMA_HEALTH = "forge/InstallationHealth/v1"
INSTALLATION_KINDS = ("content", "service", "control-tool")
INSTALLATION_KIND = "service"
INSTALL_RECEIPTS_DIR = "receipts"
INSTALL_STATE_DIR = "install"
FORGE_ID = "api-sentinel-forge"
MCP_SERVER_NAME = "api-sentinel"
MCP_VERIFY_TOOL = "sentinel_health"

_PKG_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _PKG_DIR.parent.parent  # src/api_sentinel -> repo root (checkout only)
_ASSETS_DIR = _PKG_DIR / "installation" / "assets"


# --------------------------------------------------------------------------
# small io helpers (stdlib-only, tmp+rename atomic writes)
# --------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(doc: Any) -> bytes:
    return (json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n").encode()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _home() -> Path:
    return Path(os.environ.get("FORGE_HOME_OVERRIDE") or Path.home())


def installations_dir() -> Path:
    """``~/.forge/installations/`` — the account-level manifest dir The
    Forge scans (``FORGE_HOME_OVERRIDE`` kept for tests and portable
    homelab layouts, same override the shared installkit honors)."""
    return _home() / ".forge" / "installations"


# --------------------------------------------------------------------------
# declarative metadata — packaged copies survive inside wheels
# --------------------------------------------------------------------------


def bundled_json(name: str) -> dict[str, Any] | None:
    """JSON shipped with the forge: the packaged copy under
    ``installation/assets/manifest/`` first (wheels), then the package
    root and the checkout root (editable)."""
    for cand in (
        _ASSETS_DIR / "manifest" / name,
        _PKG_DIR / name,
        _REPO_DIR / name,
    ):
        data = _load_json(cand, None)
        if isinstance(data, dict):
            return data
    return None


def declared_install_fields() -> dict[str, Any]:
    """The install-2.1 declarations of this forge's ``forge.json`` — copied
    verbatim into the account manifest, never fabricated (mirrors the
    ``register`` step of ``forge_bootstrap`` and the sibling helpers):

    - ``installation_kind``: ``content``|``service``|``control-tool``.
    - ``install``: the declared surface (operations/flags/hosts/scopes/
      profiles/approval) — the orchestrator's argv builder only sends
      what is declared.
    - ``depends_on``: forge ids that must install first — declared even
      when empty: the key's presence is an explicit declaration.
    """
    cfg = bundled_json("forge.json")
    if not cfg:
        return {}
    doc: dict[str, Any] = {}
    if cfg.get("installation_kind"):
        kind = str(cfg["installation_kind"])
        if kind not in INSTALLATION_KINDS:
            raise ValueError(
                f"forge.json installation_kind {kind!r} — expected {INSTALLATION_KINDS}"
            )
        doc["installation_kind"] = kind
    if cfg.get("install"):
        doc["install"] = dict(cfg["install"])
    if "depends_on" in cfg:
        doc["depends_on"] = [str(d) for d in cfg["depends_on"]]
    return doc


# --------------------------------------------------------------------------
# install receipts — forge/InstallReceipt/v1
# --------------------------------------------------------------------------


def install_receipts_dir(ws: Workspace) -> Path:
    """``<state>/install/receipts/`` — the nested convention the
    orchestrator's receipt glob matches; kept apart from the domain
    ``<state>/receipts/`` (handoff/retention records)."""
    return ws.root / INSTALL_STATE_DIR / INSTALL_RECEIPTS_DIR


def write_receipt(ws: Workspace, receipt: dict[str, Any]) -> Path:
    """Persist a ``forge/InstallReceipt/v1``.

    The ``lifecycle`` block (``forge/InstallLifecycle/v1``) is derived at
    the write boundary from the final ``status``/``verification`` — the
    mutation lands on the caller's dict too, so the persisted receipt
    and the emitted one carry the same derived state.
    """
    from api_sentinel.installation import lifecycle

    receipt["lifecycle"] = lifecycle.receipt_block(receipt)
    ts = _utc_now().replace(":", "").replace("-", "")
    path = install_receipts_dir(ws) / f"{receipt['operation']}-{ts}.json"
    _atomic_write(path, _json_bytes(receipt))
    return path


def list_receipts(ws: Workspace) -> list[str]:
    d = install_receipts_dir(ws)
    return sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []


def created_by() -> str:
    return f"api-sentinel-forge/{__version__}"


def verification_status(checks: list[dict[str, Any]]) -> str:
    """Canonical aggregate (same rule the installkit applies): PASS only
    when every check is PASS or NOT_APPLICABLE — a BLOCKED/FAIL check
    degrades the verdict instead of disappearing."""
    return (
        "PASS"
        if all(c.get("status") in ("PASS", "NOT_APPLICABLE") for c in checks)
        else "FAIL"
    )


def base_receipt(
    *,
    operation: str,
    scope: str,
    profile: str,
    target_root: Path | None,
    state_dir: Path,
    dry_run: bool,
) -> dict[str, Any]:
    """Shared ``forge/InstallReceipt/v1`` skeleton — required fields per
    the contract (schema, forge_id, operation, scope, profile,
    target_root, dry_run, status, checks, verification, created_at,
    created_by)."""
    return {
        "schema": SCHEMA_RECEIPT,
        "forge_id": FORGE_ID,
        "operation": operation,
        "scope": scope,
        "profile": profile,
        "target_root": str(target_root) if target_root else None,
        "state_dir": str(state_dir),
        "dry_run": dry_run,
        "managed_files": [],
        "checks": [],
        "verification": {"status": "UNVERIFIED"},
        "status": "planned" if dry_run else "failed",
        "created_at": _utc_now(),
        "created_by": created_by(),
    }


# --------------------------------------------------------------------------
# account manifest — ~/.forge/installations/api-sentinel-forge.json
# --------------------------------------------------------------------------


def installation_manifest() -> dict[str, Any]:
    doc = _load_json(installations_dir() / f"{FORGE_ID}.json", None)
    return doc if isinstance(doc, dict) else {}


def register_installation(target_root: Path | None, ws: Workspace) -> tuple[Path, bool]:
    """Merge-write the account manifest (``forge/InstallationManifest/v1``).

    A CLI install runs without the bootstrap pipeline (no venv, no
    launcher shim), so this records what is true: the CLI entry point,
    the resolved python, the install source and the declared install
    surface. Existing bootstrap fields (``installed_at``, ``venv``,
    ``install_root``) are preserved — a later bootstrap rewrites them.

    Incremental (install-2.1 §7.4): when the merged document differs
    from the manifest on disk only in ``updated_at``, the file is left
    untouched — repeated installs never rewrite unchanged assets, and
    ``updated_at`` keeps naming the last *real* modification. Returns
    ``(manifest_path, changed)``.
    """
    existing = installation_manifest()
    now = _utc_now()
    doc: dict[str, Any] = dict(existing)
    install_root = target_root if target_root is not None else ws.root
    doc.update(
        {
            "schema": SCHEMA_MANIFEST,
            "forge_id": FORGE_ID,
            "package": "api_sentinel",
            "distribution": "api-sentinel-forge",
            "cli": {
                "name": "api-sentinel",
                "version_cmd": ["api-sentinel", "--version"],
            },
            "version": __version__,
            "install_root": str(install_root),
            "python": {
                "executable": sys.executable,
                "version": platform.python_version(),
                "satisfies": ">=3.11",
            },
            "updated_at": now,
            "installed_at": doc.get("installed_at", now),
            "installed_by": doc.get(
                "installed_by",
                {"agent": "api-sentinel-cli", "version": __version__},
            ),
            "source": doc.get(
                "source",
                {"kind": "project-install", "path": str(install_root)},
            ),
        }
    )
    doc.setdefault("installation_method", "cli-install")
    mcp = doc.get("mcp")
    mcp_doc = dict(mcp) if isinstance(mcp, dict) else {}
    mcp_doc.setdefault("server_name", MCP_SERVER_NAME)
    mcp_doc.setdefault("command", ["api-sentinel", "mcp", "serve"])
    mcp_doc.setdefault("verified", False)
    mcp_doc["verify_tool"] = MCP_VERIFY_TOOL
    doc["mcp"] = mcp_doc
    doc.update(declared_install_fields())
    path = installations_dir() / f"{FORGE_ID}.json"
    desired = {k: v for k, v in doc.items() if k != "updated_at"}
    previous = {k: v for k, v in existing.items() if k != "updated_at"}
    if path.exists() and previous == desired:
        return path, False
    _atomic_write(path, _json_bytes(doc))
    return path, True


def deregister_installation() -> bool:
    """Remove the account manifest — the registry must not claim an
    install that no longer exists."""
    path = installations_dir() / f"{FORGE_ID}.json"
    if path.exists():
        path.unlink()
        return True
    return False


# --------------------------------------------------------------------------
# workspace members — §5.5-lite discovery (read-only, never installed into)
# --------------------------------------------------------------------------

_MEMBER_MARKERS = (".git", "pyproject.toml", "package.json", "forge.json")
_MEMBER_SKIP_DIRS = frozenset(
    {"node_modules", "dist", "build", "target", ".venv", "venv", "__pycache__"}
)
_MEMBER_LIMIT = 64


def discover_members(root: Path) -> list[str]:
    """Independent project members directly under a workspace root —
    directories carrying ``.git``/manifest markers (depth 1, bounded,
    symlinks never followed). The sentinel installs once at the workspace
    root; members are observation targets, not install targets."""
    out: list[str] = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return out
    for child in children:
        if len(out) >= _MEMBER_LIMIT:
            break
        try:
            if not child.is_dir() or child.is_symlink():
                continue
        except OSError:
            continue
        name = child.name
        if name.startswith(".") or name in _MEMBER_SKIP_DIRS:
            continue
        if any((child / marker).exists() for marker in _MEMBER_MARKERS):
            out.append(name)
    return out


# --------------------------------------------------------------------------
# MCP verify — stdio JSON-RPC handshake (minimal port of the installkit
# probe: initialize → notifications/initialized → tools/list → tools/call)
# --------------------------------------------------------------------------


def mcp_verify(ws: Workspace, *, timeout: int = 20) -> dict[str, Any]:
    """Handshake the real ``api-sentinel mcp serve`` process. Reports
    honestly — file presence is never treated as proof the server works."""
    check_id = "mcp-handshake"
    if importlib.util.find_spec("mcp") is None:
        return {
            "id": check_id,
            "status": "UNVERIFIED",
            "detail": "extra 'mcp' not installed — handshake not tested",
        }
    exe = shutil.which("api-sentinel")
    if exe is None:
        return {
            "id": check_id,
            "status": "BLOCKED",
            "detail": "'api-sentinel' not resolvable on PATH — install incomplete",
        }
    command = [exe, "mcp", "serve", "--workspace", str(ws.root)]
    if os.name == "nt" and Path(exe).suffix.lower() in (".cmd", ".bat"):
        command = ["cmd.exe", "/d", "/c", *command]
    try:
        proc = subprocess.Popen(  # noqa: S603 — argv is the declared MCP command
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return {
            "id": check_id,
            "status": "BLOCKED",
            "detail": f"cannot spawn 'api-sentinel': {exc}",
        }
    except OSError as exc:
        return {"id": check_id, "status": "FAIL", "detail": f"cannot spawn: {exc}"}
    if proc.stdin is None or proc.stdout is None:
        proc.kill()
        return {"id": check_id, "status": "FAIL", "detail": "stdio pipes unavailable"}

    stderr_tail = bytearray()

    def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        for chunk in iter(proc.stderr.readline, b""):
            stderr_tail.extend(chunk)
            del stderr_tail[:-400]

    threading.Thread(target=_drain_stderr, daemon=True, name="mcp-stderr").start()
    try:
        out = _mcp_handshake(proc, timeout)
    except Exception as exc:  # handshake failures are FAIL, not crashes
        out = {"id": check_id, "status": "FAIL", "detail": str(exc)[:200]}
    killed = False
    if os.name == "nt" and proc.poll() is None:
        with contextlib.suppress(Exception):  # best-effort tree cleanup
            subprocess.run(  # noqa: S603 — fixed taskkill argv, os-owned path
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],  # noqa: S607
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
    try:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=3)
            killed = True
        except Exception:  # noqa: S110 — best-effort kill after terminate
            pass
    out["process"] = {
        "exit": ("clean" if proc.returncode == 0 else "killed" if killed else "terminated"),
        "returncode": proc.returncode,
        "stderr_tail": bytes(stderr_tail).decode(errors="replace").strip()[-200:] or None,
    }
    return out


def _mcp_handshake(proc: Any, timeout: int) -> dict[str, Any]:
    """initialize → initialized → tools/list (+ tools/call
    ``sentinel_health``). Returns the check dict; the caller attaches
    process-exit evidence."""
    if proc.stdin is None or proc.stdout is None:
        return {"id": "mcp-handshake", "status": "FAIL", "detail": "stdio pipes unavailable"}
    req = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "forge-verify", "version": "1"},
            },
        }
    ).encode()
    proc.stdin.write(req + b"\n")
    proc.stdin.write(
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode() + b"\n"
    )
    proc.stdin.write(
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode() + b"\n"
    )
    proc.stdin.write(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": MCP_VERIFY_TOOL, "arguments": {}},
            }
        ).encode()
        + b"\n"
    )
    proc.stdin.flush()

    lines: queue.Queue[bytes] = queue.Queue()

    def _drain() -> None:
        for ln in proc.stdout:
            lines.put(ln)

    threading.Thread(target=_drain, daemon=True).start()
    tools: list[str] = []
    got_init = False
    invoke: dict[str, Any] = {
        "tool": MCP_VERIFY_TOOL,
        "status": "UNVERIFIED",
        "detail": "no response",
    }
    want_ids = {1, 2, 3}
    deadline = time.monotonic() + timeout
    while want_ids:
        remain = deadline - time.monotonic()
        if remain <= 0:
            break
        try:
            line = lines.get(timeout=remain)
        except queue.Empty:
            break
        if not line:
            break
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid = msg.get("id")
        if mid == 1 and "result" in msg:
            got_init = True
            want_ids.discard(1)
        elif mid == 2 and "result" in msg:
            tools = [t.get("name", "?") for t in msg["result"].get("tools", [])]
            want_ids.discard(2)
        elif mid == 3:
            want_ids.discard(3)
            if "error" in msg:
                invoke["status"] = "FAIL"
                invoke["detail"] = str(msg["error"])[:200]
            else:
                result = msg.get("result") or {}
                if result.get("isError"):
                    invoke["status"] = "FAIL"
                    invoke["detail"] = "tool reported isError"
                else:
                    invoke["status"] = "PASS"
                    invoke["detail"] = "structured result returned"
    if not got_init:
        return {"id": "mcp-handshake", "status": "FAIL", "detail": "no initialize response"}
    return {
        "id": "mcp-handshake",
        "status": "PASS",
        "detail": f"{len(tools)} tools",
        "tools": tools[:50],
        "invoke": invoke,
    }


# --------------------------------------------------------------------------
# install health — status/doctor aggregates (forge/InstallationHealth/v1)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallState:
    """What the filesystem proves about the install — never inferred."""

    state_dir_present: bool
    config_present: bool
    db_present: bool
    token_present: bool
    migrations_applied: int | None  # None = unreadable db
    migrations_discovered: int
    context_registered: bool | None  # None = no context layer expected
    account_registered: bool


def probe_state(ws: Workspace, *, context_root: Path | None) -> InstallState:
    """Read-only probe — every field is an observed fact or None."""
    from api_sentinel.infrastructure.storage.migrations import (
        applied_versions,
        discover,
    )

    config_present = ws.config_path.exists()
    db_present = ws.db_path.exists()
    applied: int | None = None
    if db_present:
        try:
            from api_sentinel.infrastructure.storage.db import connect

            conn = connect(ws.db_path)
            try:
                applied = len(applied_versions(conn))
            finally:
                conn.close()
        except Exception:
            applied = None
    token_present = (ws.root / "token").exists()
    context_registered: bool | None = None
    if context_root is not None:
        cap = context_root / ".forge" / "context" / "capabilities" / f"{FORGE_ID}.json"
        context_registered = cap.is_file()
    account_registered = (installations_dir() / f"{FORGE_ID}.json").is_file()
    return InstallState(
        state_dir_present=ws.root.exists(),
        config_present=config_present,
        db_present=db_present,
        token_present=token_present,
        migrations_applied=applied,
        migrations_discovered=len(discover()),
        context_registered=context_registered,
        account_registered=account_registered,
    )
