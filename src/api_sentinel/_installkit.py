"""Forge Installation Contract v1 — reference engine.

Canonical source: ``the-forge/scripts/installkit/forge_installkit.py``.
Vendored per-Forge (``<pkg>/_installkit.py`` or ``scripts/``); keep the
``_INSTALLKIT_VERSION``/``_SOURCE_SHA256`` header intact so drift checks can
compare copies. stdlib-only. Deterministic. Owned-only writes — the sha256
ledger is the single source of truth for what uninstall may remove.

Documents emitted: ``forge/InstallationManifest/v1``,
``forge/InstallReceipt/v1``, ``forge/InstallationHealth/v1``,
``forge/WorkspaceInstall/v1`` (see ``the-forge/docs/portable-installation/``).

Subprocess use is confined to ``_spawn()`` and gated by ``Spec.spawn_ok`` —
a Forge whose package forbids subprocess vendors a stripped copy where
``_spawn`` raises; ``update``/``mcp_verify`` then refuse honestly instead of
silently degrading.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import stat
import sys
import tempfile
import threading
import time
import uuid
from importlib import metadata as importlib_metadata
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

_INSTALLKIT_VERSION = "1.0.6"
# Filled by the vendor step (scripts/installkit/vendor.py) — the sha256 of
# the canonical source body, so drift checks can compare vendored copies.
_SOURCE_SHA256 = "63bc9936eec43957"

SCHEMA_MANIFEST = "forge/InstallationManifest/v1"
SCHEMA_RECEIPT = "forge/InstallReceipt/v1"
SCHEMA_HEALTH = "forge/InstallationHealth/v1"
SCHEMA_WORKSPACE = "forge/WorkspaceInstall/v1"

SCOPES = ("project", "workspace", "user")
PROFILES = ("minimal", "recommended", "full")
HOSTS = ("claude", "devin", "codex", "copilot")
CHECK_STATUSES = ("PASS", "FAIL", "BLOCKED", "UNVERIFIED", "NOT_APPLICABLE")

FORGE_HOME = ".forge"  # under $HOME — the forge-neutral registry
INSTALLATIONS_DIR = "installations"
LEDGER_NAME = "install-ledger.json"
RECEIPTS_DIR = "receipts"
LOCK_NAME = ".install.lock"

# Refusal codes (contract §8) — forge-specific codes may extend these.
E_SCOPE = "FORGE-INSTALL-SCOPE-UNKNOWN"
E_HOST = "FORGE-INSTALL-HOST-UNKNOWN"
E_PROFILE = "FORGE-INSTALL-PROFILE-UNKNOWN"
E_PERM = "FORGE-INSTALL-PERMISSION-DENIED"
E_PYTHON = "FORGE-INSTALL-PYTHON-INCOMPATIBLE"
E_NOTREPO = "FORGE-INSTALL-NOT-A-REPO"
E_NOTAPPROVED = "FORGE-INSTALL-PLAN-NOT-APPROVED"
E_LOCKED = "FORGE-INSTALL-LOCKED"
E_NOTINSTALLED = "FORGE-INSTALL-NOT-INSTALLED"
E_DRIFT = "FORGE-INSTALL-DRIFT-UNREPAIRABLE"
E_UNSUPPORTED = "FORGE-INSTALL-UNSUPPORTED"
E_SPAWN = "FORGE-INSTALL-SPAWN-DISABLED"
E_VERIFY = "FORGE-INSTALL-VERIFY-FAILED"
E_UNSAFE = "FORGE-INSTALL-UNSAFE-PATH"
E_WRITE = "FORGE-INSTALL-WRITE-FAILED"


class InstallError(Exception):
    """Structured refusal — carries the contract error kind."""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail

    def document(self, forge_id: str) -> dict[str, Any]:
        return {
            "schema": SCHEMA_RECEIPT,
            "forge_id": forge_id,
            "status": "failed",
            "verification": {"status": "FAIL"},
            "error": {"kind": self.kind, "detail": self.detail},
            "checks": [],
            "managed_files": [],
        }


# `[^/\s]+` (not `[^/@\s]+`): a raw `@` *inside* userinfo must not
# truncate the redaction — `u@raw:pw@host` redacts whole, while a `@`
# after the first `/` is path content and never matches. Accepted
# trade-off: a `@` in a query without a path (`host?x=a@b`) also
# redacts — over-redacting a host in a log beats leaking a credential.
_URL_USERINFO_RE = re.compile(r"(https?://)[^/\s]+@")


def redact_url_userinfo(text: str) -> str:
    """Strip ``user:pass@`` userinfo from URLs in persisted/logged text.

    ``--dist "name @ https://user:token@host/x.whl"`` must never land in
    manifests, receipts or stdout verbatim (prompt §6.5 — nunca registrar
    valores secretos). Host and path survive so diagnostics still identify
    the wheelhouse.
    """
    return _URL_USERINFO_RE.sub(r"\1***@", text)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha(path.read_bytes())


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the existing file's mode — mkstemp always creates 0600, and
    # silently downgrading a user's 0644 doc is a destructive side effect.
    mode = stat.S_IMODE(path.stat().st_mode) if path.is_file() else None
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


_SAFE_REL_RE = re.compile(r"^(?!/)(?!~)[A-Za-z0-9._][A-Za-z0-9_./-]*$")


def _safe_rel(rel: str) -> bool:
    """A ledger/asset relative path must stay inside the install root: no
    ``..`` segments, no absolute/drive forms, no backslashes."""
    return (
        bool(_SAFE_REL_RE.match(rel))
        and "\\" not in rel
        and not any(part == ".." for part in PurePosixPath(rel).parts)
        and not re.match(r"^[A-Za-z]:", rel)
    )


def _json_bytes(doc: dict[str, Any]) -> bytes:
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _home() -> Path:
    """The user's home — test isolation via HOME/XDG_CONFIG_HOME is the
    caller's job; this function reads the resolved env once."""
    return Path(os.environ.get("FORGE_HOME_OVERRIDE") or Path.home())


def forge_home() -> Path:
    return _home() / FORGE_HOME


def installs_root() -> Path:
    return forge_home() / "installs"


def installations_dir() -> Path:
    return forge_home() / INSTALLATIONS_DIR


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _venv_bin(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def _shim_dir() -> Path:
    # ~/.local/bin on every platform — the pipx/uv convention; on Windows
    # both <cli>.cmd (cmd/PowerShell) and <cli> (Git Bash) are written.
    return _home() / ".local" / "bin"


# --------------------------------------------------------------------------
# Spec — what a Forge declares about itself
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ForgeSpec:
    """What the engine needs to know about a Forge. Everything host- or
    domain-specific enters through ``render_assets``/hooks — the engine is
    generic."""

    forge_id: str  # e.g. "spark-forge-aws" (repo name)
    package: str  # import name
    distribution: str  # pip dist name
    cli_name: str  # console script
    python_spec: str  # e.g. ">=3.10" or ">=3.12,<3.13"
    state_dir: str  # project state dir, e.g. ".sparkforge-aws"
    mcp_command: tuple[str, ...] = ()  # e.g. ("sparkforge-aws","mcp","serve")
    mcp_server_name: str | None = None  # .mcp.json key
    mcp_verify_tool: str | None = None  # no-arg read-only tool for tools/call
    version_cmd: tuple[str, ...] = ("--version",)
    render_assets: Callable[[InstallContext], dict[str, bytes]] | None = None
    marker_files: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")
    marker_tag: str | None = None  # defaults to forge_id
    marker_body: str = ""
    user_state_dir: str | None = None  # e.g. "~/.sparkforge-aws"
    spawn_ok: bool = True  # False where subprocess is banned
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def tag(self) -> str:
        return self.marker_tag or self.forge_id


@dataclass
class InstallContext:
    spec: ForgeSpec
    scope: str
    root: Path  # resolved target root (project/workspace) or $HOME
    state_dir: Path  # <root>/<spec.state_dir> for project/workspace,
    # <state_dir under HOME> for user
    profile: str
    hosts: tuple[str, ...]
    dry_run: bool = False
    ledger: Ledger | None = None
    # Optional-component selection (wizard/CLI). ``asset_kinds`` narrows the
    # profile's kinds; other booleans (e.g. ``graph_studio``) are runtime
    # flags persisted to ``components.json`` under state_dir.
    options: dict[str, Any] = field(default_factory=dict)
    # Relative managed paths the consolidated workspace plan excludes from
    # this forge's write set (install-2.1: a cross-forge conflict is never
    # last-writer-wins — the loser skips the path and the receipt says so).
    # Orchestrator-set only; plan/apply report the skip, never silently drop.
    exclude: tuple[str, ...] = ()
    # Resolved argv for the managed ``.mcp.json`` key — an absolute
    # interpreter/shim path so a host never launches the forge through a
    # PATH lookup. ``None`` falls back to ``spec.mcp_command`` (declared,
    # possibly PATH-dependent — the doctor audit reports that honestly).
    mcp_command_resolved: tuple[str, ...] | None = None


# --------------------------------------------------------------------------
# Ledger — sha256-keyed ownership of managed files
# --------------------------------------------------------------------------


class Ledger:
    """Records every managed write under a target root.

    ``entries[path] = {sha256, kind, action, managed}`` — only ``managed``
    entries are removable by uninstall; ``adopted`` entries are tracked for
    drift but never deleted."""

    def __init__(self, path: Path):
        self.path = path
        self.doc: dict[str, Any] = _load_json(path, None) or {
            "schema": "forge/InstallLedger/v1",
            "entries": {},
        }

    @classmethod
    def load(cls, state_dir: Path) -> Ledger:
        return cls(state_dir / LEDGER_NAME)

    def save(self) -> None:
        _atomic_write(self.path, _json_bytes(self.doc))

    def record(
        self, rel: str, data: bytes, kind: str, action: str, *, managed: bool = True
    ) -> dict[str, Any]:
        entry = {
            "sha256": _sha(data),
            "kind": kind,
            "action": action,
            "managed": managed,
            "updated_at": _utc_now(),
        }
        self.doc["entries"][rel] = entry
        return {"path": rel, "sha256": entry["sha256"], "kind": kind, "action": action}

    def drop(self, rel: str) -> None:
        self.doc["entries"].pop(rel, None)

    def get(self, rel: str) -> dict[str, Any] | None:
        entry = self.doc["entries"].get(rel)
        return entry if isinstance(entry, dict) else None

    def entries(self) -> dict[str, Any]:
        return dict(self.doc["entries"])

    def owned_paths(self) -> list[str]:
        return sorted(p for p, e in self.doc["entries"].items() if e.get("managed"))

    def drift(
        self, root: Path, spec: "ForgeSpec | None" = None, *,
        mcp_argv: tuple[str, ...] | None = None,
    ) -> list[dict[str, str]]:
        """Per-file verdict. When ``spec`` is given the check is kind-aware:
        ``marker`` entries pass when their managed *block* is intact (user
        text outside the markers is never drift); ``mcp`` entries pass when
        the managed ``mcpServers`` key holds canonical command/args (other
        keys are user content); every other entry is compared by sha256."""
        out = []
        for rel, e in sorted(self.doc["entries"].items()):
            if not _safe_rel(rel):
                out.append({"path": rel, "status": "unsafe"})
                continue
            p = root / rel
            if not p.exists():
                out.append({"path": rel, "status": "missing"})
                continue
            if spec is not None and _entry_intact(spec, e, rel, p, mcp_argv):
                out.append({"path": rel, "status": "ok"})
            else:
                out.append({"path": rel, "status": "modified"})
        return out


def _entry_intact(
    spec: "ForgeSpec", entry: dict[str, Any], rel: str, path: Path,
    mcp_argv: tuple[str, ...] | None = None,
) -> bool:
    """Kind-aware health check for one ledger entry. Marker entries verify
    the managed block only; ``.mcp.json`` verifies the managed server key;
    everything else compares whole-file sha256."""
    kind = entry.get("kind")
    if kind == "marker" and rel in spec.marker_files and spec.marker_body:
        txt = path.read_text(encoding="utf-8")
        return apply_marker_block(txt, spec.tag, spec.marker_body) == txt
    if kind == "mcp" and spec.mcp_server_name:
        cur = _load_json(path, None)
        if not isinstance(cur, dict):
            return False
        argv = mcp_argv or spec.mcp_command
        want = {"command": argv[0], "args": list(argv[1:])}
        return bool(mcp_set(cur, spec.mcp_server_name, want, spec.tag) == cur)
    return bool(_sha_file(path) == entry["sha256"])


# --------------------------------------------------------------------------
# Locks — refuse concurrent mutation rather than racing
# --------------------------------------------------------------------------


class LockError(InstallError):
    pass


def _pid_alive(pid: int) -> bool | None:
    """Best-effort liveness; ``None`` = cannot determine (do not steal)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes  # noqa: PLC0415

            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        except Exception:
            return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None


class _Lock:
    """O_EXCL lockfile carrying ``{pid, created_at}``. Stale locks — dead
    holder or older than ``stale_after_s`` — are reclaimed once so an
    interrupted install never bricks the target."""

    def __init__(self, lock: Path, stale_after_s: float = 900.0) -> None:
        self._lock = lock
        self._stale = stale_after_s
        self._token = uuid.uuid4().hex
        self.held = False
        self.recovered = False

    def __enter__(self) -> _Lock:
        try:
            self._create()
            return self
        except FileExistsError:
            holder = _load_json(self._lock) or {"pid": None}
            stale = self._is_stale(holder)
            if not stale:
                raise LockError(E_LOCKED, f"install lock held at {self._lock} ({holder})") from None
            try:
                self._lock.unlink()
            except OSError as exc:
                raise LockError(
                    E_LOCKED, f"install lock at {self._lock} could not be reclaimed"
                ) from exc
            self.recovered = True
            try:
                self._create()
            except FileExistsError:
                # A third party won the reclaim race — that is a normal lock
                # contention outcome, not an internal error.
                raise LockError(
                    E_LOCKED, f"install lock at {self._lock} re-acquired during reclaim"
                ) from None
            return self

    def _create(self) -> None:
        fd = os.open(str(self._lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(
            fd,
            _json_bytes(
                {"pid": os.getpid(), "token": self._token, "created_at": _utc_now()}
            ),
        )
        os.close(fd)
        self.held = True

    def _is_stale(self, holder: dict[str, Any]) -> bool:
        pid = holder.get("pid")
        if isinstance(pid, int) and pid != os.getpid():
            alive = _pid_alive(pid)
            if alive is False:
                return True
            if alive is True:
                return False
            # PID undecidable (e.g. Windows) — fall back to the lock's
            # declared age; mtime alone lies on fresh checkouts.
            created = holder.get("created_at")
            if isinstance(created, str):
                try:
                    from datetime import datetime, timezone

                    stamp = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=timezone.utc
                    )
                    return (datetime.now(timezone.utc) - stamp).total_seconds() > self._stale
                except ValueError:
                    pass
        try:
            age = time.time() - self._lock.stat().st_mtime
        except OSError:
            return False
        return age > self._stale

    def __exit__(self, *exc: object) -> None:
        try:
            if self.held:
                holder = _load_json(self._lock)
                # Only remove the lock file when it still carries our token —
                # after a stale reclaim the on-disk lock may belong to a
                # newer claimant, and unlinking it would admit a third.
                if isinstance(holder, dict) and holder.get("token") == self._token:
                    with contextlib.suppress(OSError):
                        self._lock.unlink()
        finally:
            self.held = False


def acquire_lock(state_dir: Path, timeout_s: float = 0.0) -> _Lock:
    """O_EXCL lockfile. Non-blocking by default — contention is a refusal,
    not a hang. Dead/expired locks are reclaimed once."""
    state_dir.mkdir(parents=True, exist_ok=True)
    return _Lock(state_dir / LOCK_NAME)


# --------------------------------------------------------------------------
# Marker blocks — delimited managed regions inside user-owned files
# --------------------------------------------------------------------------


def _markers(tag: str) -> tuple[str, str]:
    return (f"<!-- {tag}:managed:begin -->", f"<!-- {tag}:managed:end -->")


def apply_marker_block(text: str, tag: str, body: str) -> str:
    """Replace-or-append the managed block; user content outside markers is
    never touched."""
    begin, end = _markers(tag)
    block = f"{begin}\n{body.rstrip()}\n{end}"
    if begin in text and end in text:
        pre = text[: text.index(begin)]
        post = text[text.index(end) + len(end) :]
        return pre + block + post
    sep = "" if text.endswith("\n\n") or not text else "\n\n" if text.endswith("\n") else "\n\n"
    return text + sep + block + "\n" if text else block + "\n"


def remove_marker_block(text: str, tag: str) -> str:
    begin, end = _markers(tag)
    if begin in text and end in text:
        pre = text[: text.index(begin)]
        post = text[text.index(end) + len(end) :]
        return (pre.rstrip("\n") + "\n" + post.lstrip("\n")).strip("\n") + "\n"
    return text


def marker_sha(tag: str, body: str) -> str:
    begin, end = _markers(tag)
    return _sha(f"{begin}\n{body.rstrip()}\n{end}".encode())


# --------------------------------------------------------------------------
# .mcp.json — managed key inside a shared config file
# --------------------------------------------------------------------------


def mcp_set(
    doc: dict[str, Any] | None, key: str, value: dict[str, Any], tag: str
) -> dict[str, Any]:
    """Set ``mcpServers.<key>`` inside a managed sub-block. Other servers and
    top-level keys are preserved byte-for-byte as JSON."""
    doc = dict(doc or {})
    servers = dict(doc.get("mcpServers") or {})
    servers[key] = value
    doc["mcpServers"] = servers
    managed = dict(doc.get("_forge_managed") or {})
    managed[tag] = sorted(set(managed.get(tag, [])) | {f"mcpServers.{key}"})
    doc["_forge_managed"] = managed
    return doc


def _mcp_argv_resolved(ctx: "InstallContext") -> tuple[str, ...]:
    """PATH-independent argv for the managed ``.mcp.json`` key (§9.3).

    Resolution order:

    1. the registered install manifest's ``cli.shim`` (absolute launcher
       written by the bootstrap) → ``(shim, *mcp_command[1:])``;
    2. ``(sys.executable, "-m", <package>, *mcp_command[1:])`` — the
       current interpreter, absolute by construction, module-pinned.

    Empty when the spec declares no MCP command at all.
    """
    if not ctx.spec.mcp_command:
        return ()
    head, *tail = ctx.spec.mcp_command
    if Path(head).is_absolute():
        return tuple(ctx.spec.mcp_command)
    manifest = _load_json(
        installations_dir() / f"{ctx.spec.forge_id}.json", {})
    shim = (manifest.get("cli") or {}).get("shim")
    if isinstance(shim, str) and shim and Path(shim).is_absolute():
        shim_path = Path(shim)
        if head in {shim_path.name, shim_path.stem}:
            return (shim, *tail)
        for cand in (shim_path.parent / head, shim_path.parent / (head + ".exe")):
            if cand.exists():
                return (str(cand), *tail)
    # entry-point module lookup keeps the argv a real target when the MCP
    # console script lives beside the CLI shim (e.g. ``forge-mcp``).
    try:
        import importlib.metadata as _imd

        for ep in _imd.entry_points(group="console_scripts"):
            if ep.name == head and ep.module:
                return (
                    sys.executable,
                    "-c",
                    f"import sys; from {ep.module} import {ep.attr} as _f; sys.exit(_f())",
                    *tail,
                )
    except Exception:  # noqa: BLE001 — metadata absence must not break install
        pass
    return (sys.executable, "-m", ctx.spec.package, *tail)


def _mcp_argv(ctx: "InstallContext") -> tuple[str, ...]:
    """The argv written to the managed ``.mcp.json`` key.

    ``InstallContext.mcp_command_resolved`` (explicit per-forge override)
    wins; otherwise the auto-resolved PATH-independent argv
    (:func:`_mcp_argv_resolved`) is used so hosts never depend on the
    installer's PATH."""
    resolved = ctx.mcp_command_resolved or _mcp_argv_resolved(ctx)
    return tuple(resolved or ctx.spec.mcp_command)


def mcp_unset(doc: dict[str, Any], key: str, tag: str) -> dict[str, Any]:
    doc = dict(doc or {})
    servers = dict(doc.get("mcpServers") or {})
    servers.pop(key, None)
    if servers:
        doc["mcpServers"] = servers
    else:
        doc.pop("mcpServers", None)
    managed = dict(doc.get("_forge_managed") or {})
    managed.pop(tag, None)
    if managed:
        doc["_forge_managed"] = managed
    else:
        doc.pop("_forge_managed", None)
    return doc


# --------------------------------------------------------------------------
# Scope resolution
# --------------------------------------------------------------------------


def _find_vcs_root(start: Path) -> Path | None:
    cur = start.resolve()
    for _ in range(64):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def _find_workspace_root(start: Path) -> Path | None:
    cur = start.resolve()
    for _ in range(64):
        for name in (
            "forge.workspace.yaml",
            "forge.workspace.json",
            ".forge-workspace",
            "pyproject.toml",
        ):
            if (cur / name).exists():
                if name == "pyproject.toml" and not _is_workspace_pyproject(cur):
                    cur = cur.parent
                    continue
                return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def _is_workspace_pyproject(root: Path) -> bool:
    try:
        txt = (root / "pyproject.toml").read_text(encoding="utf-8")
        return "tool.forge.workspace" in txt or "tool.uv.workspace" in txt
    except OSError:
        return False


def _find_state_root(spec: ForgeSpec, cwd: Path) -> Path | None:
    """Nearest ancestor (or cwd) already carrying `spec.state_dir` — the dir
    is an installed project even without a VCS checkout."""
    cur = Path(cwd).resolve()
    while True:
        if (cur / spec.state_dir).is_dir():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


def resolve_scope(spec: ForgeSpec, scope: str, cwd: Path, explicit: Path | None) -> Path:
    if scope not in SCOPES:
        raise InstallError(E_SCOPE, f"scope {scope!r} — expected {SCOPES}")
    if scope == "user":
        return _home()
    if scope == "workspace":
        if explicit is not None:
            # --root is authoritative: a marker may only confirm the explicit
            # root is a workspace root — the ancestor walk can never return a
            # root ABOVE it.
            return explicit
        ws = _find_workspace_root(cwd)
        return ws or (_find_vcs_root(cwd) or cwd)
    root = explicit or _find_vcs_root(cwd)
    if root is None:
        state_root = _find_state_root(spec, cwd)
        if state_root is not None:
            return state_root
        raise InstallError(E_NOTREPO, f"{cwd}: no VCS root — pass --root or cd into a repo")
    return root


def state_dir_for(spec: ForgeSpec, scope: str, root: Path) -> Path:
    if scope == "user":
        base = spec.user_state_dir or f"~/.{spec.forge_id.replace('-', '_')}"
        return Path(os.path.expandvars(os.path.expanduser(base)))
    return root / spec.state_dir


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------


def _host_dirs(host: str, scope: str) -> list[str]:
    """Project/user roots a host reads its assets from.

    `.agents/` is the shared surface: Devin, Codex and Copilot all read it
    natively, so skills/agents are written there ONCE. Host dirs remain only
    for host-specific config (`.devin` confs/payloads, `.codex` TOML agents,
    `.github` copilot assets) — never a second copy of the same skill."""
    if scope == "user":
        return {
            "claude": ["~/.claude/skills", "~/.claude/agents"],
            "devin": ["~/.agents/skills", "~/.agents/agents", "~/.devin"],
            "codex": ["~/.codex", "~/.agents/skills", "~/.agents/agents"],
            "copilot": ["~/.agents/skills", "~/.agents/agents"],
        }.get(host, [])
    return {
        "claude": [".claude/skills", ".claude/agents"],
        "devin": [".agents/skills", ".agents/agents", ".devin"],
        "codex": [".agents/skills", ".agents/agents", ".codex"],
        "copilot": [".agents/skills", ".agents/agents", ".github/skills"],
    }.get(host, [])


def profile_asset_kinds(profile: str) -> tuple[str, ...]:
    return {
        "minimal": ("config", "mcp", "marker", "state"),
        "recommended": (
            "config", "mcp", "marker", "state", "skill", "agent",
            "knowledge", "rules", "evals",
        ),
        "full": (
            "config", "mcp", "marker", "state", "skill", "agent",
            "knowledge", "rules", "evals",
        ),
    }[profile]


def asset_kinds_for(ctx: InstallContext) -> tuple[str, ...]:
    """Profile kinds narrowed by the component selection, when present."""
    selected = ctx.options.get("asset_kinds")
    if selected is None:
        return profile_asset_kinds(ctx.profile)
    return tuple(selected)


def components_path(state_dir: Path) -> Path:
    """Where the optional-component selection is persisted."""
    return state_dir / "components.json"


# Optional components selectable beyond the profile. Public names map to
# asset kinds; "graph-studio"/"tui" are runtime flags persisted to
# components.json. Core kinds are always installed.
COMPONENT_KINDS = {
    "skills": "skill",
    "agents": "agent",
    "mcp": "mcp",
    "knowledge": "knowledge",
    "rules": "rules",
    "evals": "evals",
}
CORE_KINDS = ("config", "marker", "state")
OPTIONAL_COMPONENTS = (
    "skills",
    "agents",
    "mcp",
    "knowledge",
    "rules",
    "evals",
    "tui",
    "graph-studio",
)


def component_options(
    profile: str, components: tuple[str, ...] | list[str] | None
) -> dict[str, Any]:
    """Component list → InstallContext.options. None = profile default."""
    if components is None:
        return {}
    selected = set(components)
    unknown = selected - set(OPTIONAL_COMPONENTS)
    if unknown:
        raise InstallError(
            E_PROFILE,
            f"unknown components {sorted(unknown)}; known: {list(OPTIONAL_COMPONENTS)}",
        )
    kinds = list(CORE_KINDS)
    for comp, kind in COMPONENT_KINDS.items():
        if comp in selected and kind not in kinds:
            kinds.append(kind)
    return {
        "asset_kinds": kinds,
        "graph_studio": "graph-studio" in selected,
        "tui": "tui" in selected,
    }


def load_components(state_dir: Path) -> dict[str, Any]:
    """{} when never recorded — absence means default (all enabled)."""
    return _load_json(components_path(state_dir), {})


def plan(ctx: InstallContext) -> dict[str, Any]:
    """Pure plan — what *would* change. Never writes."""
    assets = ctx.spec.render_assets(ctx) if ctx.spec.render_assets else {}
    kinds = set(asset_kinds_for(ctx))
    excluded = set(ctx.exclude)
    files = []
    for rel in sorted(assets):
        kind = _asset_kind(rel)
        if kind not in kinds:
            continue
        if not _safe_rel(rel):
            raise InstallError(
                E_UNSAFE,
                f"{rel!r}: escapes the install root — remove or fix the asset path",
            )
        if rel in excluded:
            files.append(
                {
                    "path": rel,
                    "kind": kind,
                    "action": "skipped",
                    "sha256": _sha(assets[rel]),
                    "note": "cross-forge conflict — excluded by the consolidated plan",
                }
            )
            continue
        target = ctx.root / rel
        exists = target.exists()
        files.append(
            {
                "path": rel,
                "kind": kind,
                "action": "update" if exists else "create",
                "sha256": _sha(assets[rel]),
            }
        )
    markers = [
        {"file": m, "marker": ctx.spec.tag}
        for m in ctx.spec.marker_files
        if (ctx.root / m).exists() or m == "AGENTS.md"
    ]
    return {
        "schema": SCHEMA_RECEIPT,
        "forge_id": ctx.spec.forge_id,
        "operation": "install",
        "scope": ctx.scope,
        "profile": ctx.profile,
        "components": dict(ctx.options) or None,
        "target_root": str(ctx.root),
        "hosts": list(ctx.hosts),
        "dry_run": ctx.dry_run,
        "status": "planned",
        "planned_files": files,
        "planned_markers": markers,
        "managed_files": [],
        "checks": [],
        "verification": {"status": "UNVERIFIED"},
    }


def _asset_kind(rel: str) -> str:
    if "/skills/" in f"/{rel}" or rel.endswith("SKILL.md"):
        return "skill"
    if "/agents/" in f"/{rel}" or "agent" in Path(rel).name.lower():
        return "agent"
    if "/knowledge/" in f"/{rel}":
        return "knowledge"
    if "/rules/" in f"/{rel}":
        return "rules"
    if "/evals/" in f"/{rel}":
        return "evals"
    if Path(rel).name == ".mcp.json" or "mcp" in rel:
        return "mcp"
    if rel.startswith((".", "")) and Path(rel).suffix in (".json", ".yaml", ".yml", ".toml"):
        return "config"
    return "state"


def _rollback_writes(ctx: InstallContext, created: list[Path], backups: dict[Path, Path]) -> None:
    """§14.3.7 — revert managed writes from a failed apply: restore pre-state
    backups, remove files created by the operation. User-owned files were
    never touched (conflicts refuse the plan before this point), so rollback
    can only undo our own writes."""
    for target, bak in backups.items():
        try:
            _atomic_write(target, bak.read_bytes())
        except OSError:
            pass
    removed: list[str] = []
    for target in created:
        try:
            target.unlink()
            removed.append(str(target.relative_to(ctx.root)))
        except (OSError, ValueError):
            pass
    _prune_empty_dirs(ctx.root, removed)


def apply_install(ctx: InstallContext, *, approved: bool = False) -> dict[str, Any]:
    """The governed write path. Idempotent: unchanged assets are not
    re-touched; pre-existing identical files are adopted, never owned."""
    if not approved and not ctx.dry_run:
        raise InstallError(
            E_NOTAPPROVED, "install requires --yes/--approve or --dry-run; the plan is the contract"
        )
    p = plan(ctx)
    if ctx.dry_run:
        p["status"] = "planned"
        return p

    files: list[dict[str, Any]] = []
    markers: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    ledger = ctx.ledger or Ledger.load(ctx.state_dir)
    ledger_before = ledger.path.read_bytes() if ledger.path.exists() else None
    component_file = components_path(ctx.state_dir) if ctx.options else None
    component_before = (
        component_file.read_bytes()
        if component_file is not None and component_file.exists()
        else None
    )
    # §14.3 journal — every write is either a creation (removable) or an
    # overwrite with a pre-state snapshot; on failure the managed half is
    # reverted before the error propagates.
    created: list[Path] = []
    backups: dict[Path, Path] = {}

    def _snapshot(target: Path, rel: str) -> None:
        bak = ctx.state_dir / "backups" / rel
        bak.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(bak, target.read_bytes())
        backups[target] = bak

    try:
        assets = ctx.spec.render_assets(ctx) if ctx.spec.render_assets else {}
        kinds = set(asset_kinds_for(ctx))
        ctx_bytes = {
            "skill": 0, "agent": 0, "knowledge": 0, "rules": 0, "evals": 0,
            "managed": 0,
        }
        # Incremental install (cycle 16.1 §7): the receipt must say how many
        # assets were written vs. already byte-identical — reinstalls do not
        # re-touch unchanged managed files.
        written_bytes = 0
        excluded = set(ctx.exclude)
        for rel in sorted(assets):
            kind = _asset_kind(rel)
            if kind not in kinds:
                continue
            if not _safe_rel(rel):
                raise InstallError(
                    E_UNSAFE,
                    f"{rel!r}: escapes the install root — remove or fix the asset path",
                )
            if rel in excluded:
                # Loser path of a cross-forge conflict — the consolidated
                # plan already assigned the winner. Skip and say so.
                files.append(
                    {
                        "path": rel,
                        "kind": kind,
                        "action": "skipped",
                        "sha256": _sha(assets[rel]),
                        "note": "cross-forge conflict — skipped by the consolidated plan",
                    }
                )
                continue
            data = assets[rel]
            if kind in ctx_bytes:
                ctx_bytes[kind] += len(data)
            ctx_bytes["managed"] += len(data)
            target = ctx.root / rel
            existing_entry = ledger.get(rel)
            if target.exists():
                cur = target.read_bytes()
                if cur == data:
                    action = "unchanged" if existing_entry else "adopted"
                    files.append(
                        ledger.record(rel, data, kind, action, managed=bool(existing_entry))
                    )
                    continue
                if existing_entry is None:
                    # Pre-existing foreign file — never overwrite silently.
                    files.append(
                        {
                            "path": rel,
                            "sha256": _sha(cur),
                            "kind": kind,
                            "action": "unchanged",
                            "note": "user-owned, not managed — skipped",
                        }
                    )
                    continue
                _snapshot(target, rel)
            else:
                created.append(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, data)
            written_bytes += len(data)
            files.append(ledger.record(rel, data, kind, "updated" if existing_entry else "created"))

        # Marker blocks in AGENTS.md / CLAUDE.md — delimited, user content kept.
        if "marker" in kinds and ctx.spec.marker_body:
            for m in ctx.spec.marker_files:
                target = ctx.root / m
                if not target.exists() and m != "AGENTS.md":
                    continue  # never create CLAUDE.md unprompted; AGENTS.md is the generic contract
                original = target.read_text(encoding="utf-8") if target.exists() else ""
                updated = apply_marker_block(original, ctx.spec.tag, ctx.spec.marker_body)
                if updated == original:
                    markers.append(
                        {
                            "file": m,
                            "marker": ctx.spec.tag,
                            "sha256": marker_sha(ctx.spec.tag, ctx.spec.marker_body),
                        }
                    )
                    continue
                if target.exists():
                    _snapshot(target, m)
                else:
                    created.append(target)
                _atomic_write(target, updated.encode("utf-8"))
                ledger.record(
                    m, updated.encode(), "marker", "updated" if ledger.get(m) else "created"
                )
                markers.append(
                    {
                        "file": m,
                        "marker": ctx.spec.tag,
                        "sha256": marker_sha(ctx.spec.tag, ctx.spec.marker_body),
                    }
                )

        # .mcp.json managed key — only when the forge has an MCP server.
        mcp_entries: list[dict[str, Any]] = []
        if ctx.spec.mcp_server_name and "mcp" in kinds:
            mcp_file = ctx.root / ".mcp.json"
            argv = _mcp_argv(ctx)
            entry = {"command": argv[0], "args": list(argv[1:])}
            if mcp_file.exists():
                cur = _load_json(mcp_file, None)
                if cur is None:
                    checks.append(
                        {
                            "id": "mcp-json",
                            "status": "FAIL",
                            "detail": ".mcp.json exists but is not JSON",
                        }
                    )
                else:
                    new = mcp_set(cur, ctx.spec.mcp_server_name, entry, ctx.spec.tag)
                    if _json_bytes(new) != mcp_file.read_bytes():
                        _snapshot(mcp_file, ".mcp.json")
                        _atomic_write(mcp_file, _json_bytes(new))
                        ledger.record(
                            ".mcp.json",
                            _json_bytes(new),
                            "mcp",
                            "updated" if ledger.get(".mcp.json") else "created",
                        )
                    mcp_entries.append(
                        {
                            "server": ctx.spec.mcp_server_name,
                            "file": ".mcp.json",
                            "key": f"managed:{ctx.spec.tag}",
                            "action": "set",
                            "argv": list(argv),
                        }
                    )
            else:
                new = mcp_set(None, ctx.spec.mcp_server_name, entry, ctx.spec.tag)
                created.append(mcp_file)
                _atomic_write(mcp_file, _json_bytes(new))
                ledger.record(".mcp.json", _json_bytes(new), "mcp", "created")
                mcp_entries.append(
                    {
                        "server": ctx.spec.mcp_server_name,
                        "file": ".mcp.json",
                        "key": f"managed:{ctx.spec.tag}",
                        "action": "created",
                        "argv": list(argv),
                    }
                )
    except InstallError:
        _rollback_writes(ctx, created, backups)
        raise
    except Exception as exc:
        _rollback_writes(ctx, created, backups)
        raise InstallError(
            E_WRITE, f"install failed at write stage; managed writes reverted: {exc}"
        ) from exc

    try:
        ledger.save()
        if ctx.options:
            ctx.state_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write(component_file, _json_bytes(dict(ctx.options)))
    except InstallError:
        _rollback_writes(ctx, created, backups)
        raise
    except Exception as exc:
        _rollback_writes(ctx, created, backups)
        try:
            if ledger_before is None:
                ledger.path.unlink(missing_ok=True)
            else:
                _atomic_write(ledger.path, ledger_before)
            if component_file is not None:
                if component_before is None:
                    component_file.unlink(missing_ok=True)
                else:
                    _atomic_write(component_file, component_before)
        except OSError:
            pass
        raise InstallError(
            E_WRITE, f"install failed at state stage; managed writes reverted: {exc}"
        ) from exc
    written = sum(1 for f in files if f.get("action") in ("created", "updated"))
    checks.append({"id": "files-written", "status": "PASS", "detail": f"{written} writes"})
    conflict_skips = [f["path"] for f in files if f.get("action") == "skipped"]
    if conflict_skips:
        checks.append(
            {
                "id": "cross-forge-conflicts",
                "status": "PASS",
                "detail": (
                    f"{len(conflict_skips)} loser paths skipped per the "
                    f"consolidated plan: {conflict_skips}"
                ),
            }
        )
    if "codex" in ctx.hosts and ctx.scope == "project":
        # Observed on codex-cli 0.160.0: `codex mcp list` ignores project
        # `.mcp.json` and `.codex/config.toml` — MCP only enters through
        # `~/.codex/config.toml` (user scope) or `codex mcp add`.
        checks.append(
            {
                "id": "mcp-scope:codex",
                "status": "NOT_APPLICABLE",
                "detail": "codex does not read project .mcp.json; MCP needs "
                          "--scope user or `codex mcp add`",
            }
        )
    counts: dict[str, int] = {}
    for item in files:
        action = item.get("action", "?")
        counts[action] = counts.get(action, 0) + 1
    counts["written"] = counts.get("created", 0) + counts.get("updated", 0)
    receipt = {
        "schema": SCHEMA_RECEIPT,
        "receipt_id": f"sha256:{_sha(_json_bytes({'f': files, 'm': markers}))}",
        "forge_id": ctx.spec.forge_id,
        "operation": "install",
        "scope": ctx.scope,
        "profile": ctx.profile,
        "components": dict(ctx.options) or None,
        "target_root": str(ctx.root),
        "host": ",".join(ctx.hosts) or None,
        "dry_run": False,
        "managed_files": files,
        "managed_markers": markers,
        "mcp": mcp_entries,
        # created/updated/unchanged/adopted/skipped + ``written`` (real
        # byte writes) — incremental-install accounting, honest zeros.
        "counts": counts | {"written_bytes": written_bytes},
        # Context-budget metrics — only what is observable at write time
        # (§13.2). Token counts are never invented: hosts that supply
        # them report through their own telemetry.
        "context": {
            "profile": ctx.profile,
            "skills_bytes": ctx_bytes["skill"],
            "agents_bytes": ctx_bytes["agent"],
            "managed_bytes": ctx_bytes["managed"] or None,
            "mcp_entries": len(mcp_entries) or None,
        },
        "env": {"python": platform.python_version()},
        "checks": checks,
        "verification": {
            "status": "PASS"
            if all(c["status"] in ("PASS", "NOT_APPLICABLE") for c in checks)
            else "FAIL"
        },
        "status": "completed",
        "rollback": {"available": bool(files), "restore": "ledger"},
        "created_at": _utc_now(),
        "created_by": _created_by(ctx.spec.distribution),
    }
    _write_receipt(ctx.state_dir, receipt)
    return receipt


def _created_by(distribution: str) -> str:
    """``dist/version`` when the dist metadata is installed, bare ``dist``
    otherwise — matches service.py's ``f"theforge/{__version__}"`` shape."""
    try:
        return f"{distribution}/{importlib_metadata.version(distribution)}"
    except importlib_metadata.PackageNotFoundError:
        return distribution


SCHEMA_LIFECYCLE = "forge/InstallLifecycle/v1"

_BLOCKED_STATUSES = frozenset({"blocked", "refused", "unsupported", "declined"})
"""Receipt ``status`` values that mean a gate refused the operation."""


def _receipt_lifecycle(doc: dict[str, Any]) -> dict[str, Any]:
    """Derive the ``forge/InstallLifecycle/v1`` block of a receipt.

    The state is a *view* over ``status`` + ``verification`` — the same
    closed derivation the-forge applies in ``install/lifecycle.py`` so a
    reader recomputing it reaches the same answer. ``planned`` →
    ``applied`` → ``verified`` is the success line; gate refusals are
    ``blocked``, errors ``failed``, evidence gaps ``unverified``.
    """
    status = str(doc.get("status") or "")
    verification = str((doc.get("verification") or {}).get("status") or "")
    if status == "planned":
        state, detail = "planned", "plan emitted — nothing written"
    elif status in _BLOCKED_STATUSES:
        state, detail = "blocked", f"gate refusal: {status}"
    elif status == "completed":
        if verification == "PASS":
            state, detail = "verified", "writes executed and receipt checks pass"
        elif verification == "FAIL":
            state, detail = "failed", "writes executed but verification failed"
        else:
            state, detail = (
                "applied",
                f"writes executed; verification {verification or 'absent'}",
            )
    elif status == "failed":
        checks = [c for c in doc.get("checks") or [] if isinstance(c, dict)]
        if checks and all(
            c.get("status") in ("BLOCKED", "UNVERIFIED", "NOT_APPLICABLE") for c in checks
        ):
            state, detail = "blocked", "every check gated — nothing applied"
        else:
            state, detail = "failed", "operation errored"
    elif status == "unverified" or not status:
        state, detail = "unverified", "no operation evidence"
    else:
        state, detail = (
            "unverified",
            f"status {status!r} outside the known receipt vocabulary",
        )
    return {"schema": SCHEMA_LIFECYCLE, "state": state, "detail": detail}


def _write_receipt(state_dir: Path, receipt: dict[str, Any]) -> Path:
    ts = _utc_now().replace(":", "").replace("-", "")
    # derived at write time — after the final status/verification is known
    receipt["lifecycle"] = _receipt_lifecycle(receipt)
    path = state_dir / RECEIPTS_DIR / f"{receipt['operation']}-{ts}.json"
    _atomic_write(path, _json_bytes(receipt))
    return path


# --------------------------------------------------------------------------
# status / doctor / repair / uninstall
# --------------------------------------------------------------------------


def status(ctx: InstallContext) -> dict[str, Any]:
    ledger = Ledger.load(ctx.state_dir)
    drift = ledger.drift(ctx.root, ctx.spec, mcp_argv=_mcp_argv(ctx))
    ok = sum(1 for d in drift if d["status"] == "ok")
    bad = [d for d in drift if d["status"] != "ok"]
    receipts = (
        sorted((ctx.state_dir / RECEIPTS_DIR).glob("*.json"))
        if (ctx.state_dir / RECEIPTS_DIR).is_dir()
        else []
    )
    return {
        "schema": SCHEMA_HEALTH,
        "forge_id": ctx.spec.forge_id,
        "scope": ctx.scope,
        "target_root": str(ctx.root),
        "state_dir": str(ctx.state_dir),
        "ledger_entries": len(ledger.entries()),
        "drift": {
            "ok": ok,
            "modified": [d for d in bad if d["status"] == "modified"],
            "missing": [d for d in bad if d["status"] == "missing"],
            "unsafe": [d for d in bad if d["status"] == "unsafe"],
        },
        "receipts": [r.name for r in receipts],
        "forge_portal": forge_portal_presence(),
        "status": ("healthy" if not bad else "degraded" if ok else "broken")
        if ledger.entries()
        else "unverified",
        "checked_at": _utc_now(),
    }


def doctor(ctx: InstallContext) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ledger = Ledger.load(ctx.state_dir)
    installed = ledger.entries()
    checks.append(
        {
            "id": "ledger",
            "status": "PASS" if installed else "UNVERIFIED",
            "detail": f"{len(installed)} managed entries",
            "repairable": False,
        }
    )
    for d in ledger.drift(ctx.root, ctx.spec, mcp_argv=_mcp_argv(ctx)):
        if d["status"] != "ok":
            unsafe = d["status"] == "unsafe"
            checks.append(
                {
                    "id": f"drift:{d['path']}",
                    "status": "FAIL",
                    "detail": (
                        "unsafe — ledger key escapes the install root; "
                        "repair skips it: edit or rebuild the ledger"
                        if unsafe
                        else d["status"]
                    ),
                    "repairable": not unsafe,
                }
            )
    if ctx.spec.mcp_server_name:
        mcp_file = ctx.root / ".mcp.json"
        if mcp_file.exists():
            doc = _load_json(mcp_file, None)
            ok = isinstance(doc, dict) and ctx.spec.mcp_server_name in doc.get("mcpServers", {})
            checks.append(
                {"id": "mcp-config", "status": "PASS" if ok else "FAIL", "repairable": not ok}
            )
        else:
            checks.append(
                {
                    "id": "mcp-config",
                    "status": "FAIL",
                    "detail": ".mcp.json missing",
                    "repairable": True,
                }
            )
        # The probe exercises the argv the managed key actually declares —
        # a resolved absolute path, never a PATH guess.
        mcp_check = mcp_verify(replace(ctx.spec, mcp_command=_mcp_argv(ctx)))
        checks.append(mcp_check)
        invoke = mcp_check.get("invoke")
        if isinstance(invoke, dict):  # hoisted so a FAIL degrades health
            checks.append(
                {
                    "id": "mcp-invoke",
                    "status": invoke["status"],
                    "detail": f"{invoke['tool']}: {invoke['detail']}",
                    "repairable": False,
                }
            )
    presence = forge_portal_presence()
    found = presence["state"] == "installed"
    checks.append(
        {
            "id": "forge-portal",
            "status": "PASS" if found else "NOT_APPLICABLE",
            "detail": presence["install_root"]
            or ("vendored assets only — no local forge-portal install found"
                if not found
                else presence["manifest"]),
            "repairable": False,
        }
    )
    overall = "healthy"
    if any(c["status"] == "FAIL" for c in checks):
        overall = "degraded" if installed else "broken"
    elif not installed:
        overall = "unverified"
    handshake = next((c for c in checks if c.get("id") == "mcp-handshake"), None)
    return {
        "schema": SCHEMA_HEALTH,
        "forge_id": ctx.spec.forge_id,
        "status": overall,
        "checks": checks,
        # Observable context budget only (§13.2): tools the server
        # actually enumerated; nothing inferred.
        "context": {
            "profile": ctx.profile,
            "managed_entries": len(installed),
            "tools_exposed": len(handshake["tools"])
            if isinstance(handshake, dict) and isinstance(handshake.get("tools"), list)
            else None,
        },
        "checked_at": _utc_now(),
        "repair_hint": f"{ctx.spec.cli_name} repair"
        if any(c.get("repairable") for c in checks)
        else None,
    }


def _repair_content(
    ctx: InstallContext, rel: str, entry: dict[str, Any], assets: dict[str, bytes], target: Path
) -> bytes | None:
    """Canonical bytes for a drifted *managed* entry, or ``None`` when the
    entry cannot be re-asserted. Marker entries re-heal the managed block
    inside the user's file (content outside the markers survives);
    ``.mcp.json`` re-sets the managed server key preserving sibling keys;
    whole-file managed assets re-assert the rendered bytes."""
    kind = entry.get("kind")
    if kind == "marker" and rel in ctx.spec.marker_files:
        original = target.read_text(encoding="utf-8") if target.exists() else ""
        return apply_marker_block(original, ctx.spec.tag, ctx.spec.marker_body).encode("utf-8")
    if kind == "mcp" and ctx.spec.mcp_server_name:
        cur = _load_json(target, {})
        if not isinstance(cur, dict):
            cur = {}
        argv = _mcp_argv(ctx)
        want = {"command": argv[0], "args": list(argv[1:])}
        new = mcp_set(cur, ctx.spec.mcp_server_name, want, ctx.spec.tag)
        return _json_bytes(new)
    return assets.get(rel)


def repair(ctx: InstallContext) -> dict[str, Any]:
    """Re-assert drifted *managed* content: missing entries are restored,
    damaged managed regions (marker block, mcp key) are healed inside the
    user's file, drifted managed assets are re-asserted. Entries with no
    canonical source land in ``skipped`` as unrepairable; foreign content is
    never touched."""
    ledger = Ledger.load(ctx.state_dir)
    drift = [
        d
        for d in ledger.drift(ctx.root, ctx.spec, mcp_argv=_mcp_argv(ctx))
        if d["status"] != "ok"
    ]
    fixed, kept, skipped = [], [], []
    assets = ctx.spec.render_assets(ctx) if ctx.spec.render_assets else {}
    for d in drift:
        rel = d["path"]
        entry = ledger.get(rel)
        if entry is None or not _safe_rel(rel):
            skipped.append(rel)
            continue
        target = ctx.root / rel
        data = _repair_content(ctx, rel, entry, assets, target)
        if data is None:
            skipped.append(rel)
            continue
        if target.exists() and target.read_bytes() == data:
            kept.append(rel)
            continue
        if target.exists():
            bak = ctx.state_dir / "backups" / rel
            bak.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(bak, target.read_bytes())
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, data)
        ledger.record(
            rel, data, entry["kind"], "restored" if d["status"] == "missing" else "rehealed"
        )
        fixed.append(rel)
    ledger.save()
    return {
        "schema": SCHEMA_RECEIPT,
        "forge_id": ctx.spec.forge_id,
        "operation": "repair",
        "scope": ctx.scope,
        "target_root": str(ctx.root),
        "repaired": fixed,
        "kept": kept,
        "skipped": skipped,
        "managed_files": [],
        "checks": [
            {
                "id": "repair",
                "status": "PASS" if not skipped else "FAIL",
                "detail": (f"{len(fixed)} restored, {len(kept)} kept, {len(skipped)} skipped"),
            }
        ],
        "verification": {"status": "PASS" if not skipped else "FAIL"},
        "status": "completed",
        "created_at": _utc_now(),
    }


def uninstall(ctx: InstallContext, *, purge_state: bool = False) -> dict[str, Any]:
    """Remove only ledger-owned files. Adopted and user files survive;
    marker blocks are excised; the .mcp.json managed key is unset."""
    ledger = Ledger.load(ctx.state_dir)
    removed, kept = [], []
    for rel in ledger.owned_paths():
        if not _safe_rel(rel):
            # A poisoned ledger key must never resolve into an unlink outside
            # the install root — keep it (and the entry) for repair/triage.
            kept.append(rel)
            continue
        # `.mcp.json` is a shared document. Its managed server key is removed
        # below while sibling servers and user metadata are preserved; the
        # generic whole-file deletion path must never win first.
        if rel == ".mcp.json" and ctx.spec.mcp_server_name:
            continue
        target = ctx.root / rel
        if rel in ctx.spec.marker_files and target.exists():
            # marker files: excise the block, keep the file
            txt = remove_marker_block(target.read_text(encoding="utf-8"), ctx.spec.tag)
            if txt.strip():
                _atomic_write(target, txt.encode())
                kept.append(rel)
            else:
                target.unlink()
                removed.append(rel)
            ledger.drop(rel)
            continue
        if target.exists():
            # Re-verify sha256 before deleting — a user-modified managed
            # file is treated as user-owned and kept.
            reg = ledger.get(rel)
            if reg is None or _sha_file(target) != reg["sha256"]:
                kept.append(rel)
                ledger.drop(rel)
                continue
            target.unlink()
            removed.append(rel)
        ledger.drop(rel)
    if ctx.spec.mcp_server_name:
        mcp_file = ctx.root / ".mcp.json"
        if mcp_file.exists():
            doc = _load_json(mcp_file, None)
            if isinstance(doc, dict):
                new = mcp_unset(doc, ctx.spec.mcp_server_name, ctx.spec.tag)
                if new.get("mcpServers") or len(new) > 0:
                    _atomic_write(mcp_file, _json_bytes(new))
                else:
                    mcp_file.unlink()
                ledger.drop(".mcp.json")
    _prune_empty_dirs(ctx.root, removed)
    if purge_state and ctx.state_dir.exists():
        import shutil

        shutil.rmtree(ctx.state_dir, ignore_errors=True)
        # prune now-empty forge state ancestors (e.g. .forge-doctor-data/)
        parent = ctx.state_dir.parent
        while parent != ctx.root and ctx.root in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    else:
        ledger.save()
    receipt = {
        "schema": SCHEMA_RECEIPT,
        "receipt_id": f"sha256:{_sha(_json_bytes({'r': removed, 'k': kept}))}",
        "forge_id": ctx.spec.forge_id,
        "operation": "uninstall",
        "scope": ctx.scope,
        "target_root": str(ctx.root),
        "removed": removed,
        "kept": kept,
        "managed_files": [],
        "checks": [
            {
                "id": "uninstall",
                "status": "PASS",
                "detail": f"{len(removed)} removed, {len(kept)} kept",
            }
        ],
        "verification": {"status": "PASS"},
        "status": "completed",
        "created_at": _utc_now(),
    }
    if not purge_state:
        _write_receipt(ctx.state_dir, receipt)
    return receipt


def _prune_empty_dirs(root: Path, removed: list[str]) -> None:
    """Remove empty parent dirs of deleted managed files, stopping at the
    first non-empty ancestor or ``root``. User dirs are never touched —
    ``rmdir`` refuses on non-empty directories by design."""
    seen: set[Path] = set()
    resolved_root = root.resolve()
    for rel in removed:
        # Entries may be ledger-relative keys or already-absolute paths
        # (detach receipts). Both must resolve INSIDE root before rmdir.
        try:
            fpath = Path(rel) if Path(rel).is_absolute() else root / rel
            fpath.resolve().relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        parent = fpath.resolve().parent
        while parent != resolved_root and resolved_root in parent.parents \
                and parent not in seen:
            try:
                parent.rmdir()
            except OSError:
                break
            seen.add(parent)
            parent = parent.parent


# --------------------------------------------------------------------------
# ~/.forge registry — the forge-neutral installations directory
# --------------------------------------------------------------------------


# Declared install-surface keys of ``forge/InstallationManifest/v1`` —
# optional additions consumed by the orchestrator's typed delegation
# (install-2.1, ``forge/WorkspaceInstallation/v2``):
#
# - ``installation_kind``: ``content``|``service``|``control-tool``.
# - ``install``: declared surface — ``operations``/``flags``/``hosts``/
#   ``scopes``/``profiles``/``approval``. The orchestrator's argv builder
#   only sends what is declared; undeclared blocks keep the v1 default
#   (the standard flag family) for backward compatibility.
# - ``depends_on``: forge_ids that must install first (install order).
INSTALLATION_KINDS = ("content", "service", "control-tool")


def manifest_for(
    spec: ForgeSpec,
    *,
    install_root: Path,
    venv: Path | None,
    version: str,
    source: dict[str, Any],
    shim: Path | None,
    installation_method: str | None = None,
    installation_kind: str | None = None,
    install: dict[str, Any] | None = None,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    doc = {
        "schema": SCHEMA_MANIFEST,
        "forge_id": spec.forge_id,
        "package": spec.package,
        "distribution": spec.distribution,
        "cli": {"name": spec.cli_name, "version_cmd": [spec.cli_name, *spec.version_cmd]},
        "version": version,
        "install_root": str(install_root),
        "python": {
            "executable": str(_venv_python(venv)) if venv else sys.executable,
            "version": platform.python_version(),
            "satisfies": spec.python_spec,
        },
        "source": source,
        "installation_method": installation_method
        or (
            "pypi-wheel"
            if source.get("kind") == "pypi"
            else (
                "git-checkout"
                if source.get("editable", source.get("kind") == "git-checkout")
                else "wheel"
            )
        ),
        "installed_at": _utc_now(),
        "installed_by": {"agent": "forge_bootstrap", "version": _INSTALLKIT_VERSION},
    }
    if venv:
        doc["venv"] = str(venv)
    if shim:
        cli_doc = doc["cli"]
        if isinstance(cli_doc, dict):
            cli_doc["shim"] = str(shim)
    if spec.mcp_server_name:
        mcp_doc: dict[str, Any] = {
            "server_name": spec.mcp_server_name,
            "command": list(spec.mcp_command),
            "verified": False,
        }
        if spec.mcp_verify_tool:
            mcp_doc["verify_tool"] = spec.mcp_verify_tool
        doc["mcp"] = mcp_doc
    if installation_kind is not None:
        if installation_kind not in INSTALLATION_KINDS:
            raise InstallError(
                E_UNSUPPORTED,
                f"installation_kind {installation_kind!r} — expected {INSTALLATION_KINDS}",
            )
        doc["installation_kind"] = installation_kind
    if install:
        # Declared install surface — copied verbatim from the forge's own
        # declaration (forge.json/bootstrap), never fabricated.
        doc["install"] = dict(install)
    if depends_on:
        doc["depends_on"] = [str(d) for d in depends_on]
    return doc


def register_installation(manifest: dict[str, Any]) -> Path:
    path = installations_dir() / f"{manifest['forge_id']}.json"
    _atomic_write(path, _json_bytes(manifest))
    return path


def deregister_installation(forge_id: str) -> bool:
    path = installations_dir() / f"{forge_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def installed_forges() -> list[dict[str, Any]]:
    out = []
    d = installations_dir()
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            doc = _load_json(f, None)
            if isinstance(doc, dict):
                doc["_manifest_path"] = str(f)
                out.append(doc)
    return out


def forge_portal_presence() -> dict[str, Any]:
    """Whether a local forge-portal exists in an already-defined location:
    ``FORGE_PORTAL_HOME`` env, ``~/.forge/installations/forge-portal.json``
    (InstallationManifest registry) or ``~/.forge/installs/forge-portal/``.
    Never fabricates paths — every reported location exists on disk."""
    locations: list[dict[str, str]] = []

    env_root = os.environ.get("FORGE_PORTAL_HOME", "").strip()
    env_ok = bool(env_root) and Path(env_root).is_dir()
    if env_ok:
        locations.append({"kind": "env:FORGE_PORTAL_HOME", "path": env_root})

    manifest_path = installations_dir() / "forge-portal.json"
    install_root: str | None = None
    installed_at: str | None = None
    manifest_ok = manifest_path.is_file()
    if manifest_ok:
        locations.append({"kind": "registry", "path": str(manifest_path)})
        doc = _load_json(manifest_path, None)
        if isinstance(doc, dict):
            install_root = doc.get("install_root") or None
            installed_at = doc.get("installed_at") or None

    if install_root and not Path(install_root).is_dir():
        install_root = None  # manifest points somewhere gone — honest
    if install_root:
        locations.append({"kind": "install_root", "path": install_root})
    elif not manifest_ok:
        default_root = installs_root() / "forge-portal"
        if default_root.is_dir():
            install_root = str(default_root)
            locations.append({"kind": "install_root", "path": install_root})

    root = install_root or (env_root if env_ok else None)
    return {
        "state": "installed" if root or manifest_ok else "vendored",
        "install_root": root,
        "manifest": str(manifest_path) if manifest_ok else None,
        "installed_at": installed_at,
        "locations": locations,
    }


# --------------------------------------------------------------------------
# Workspace members — multi-repo discovery (``forge/WorkspaceInstall/v1``)
# --------------------------------------------------------------------------


# Mirrors ``theforge.security.paths.IGNORED_DIRS`` — the vendored copy must
# stay self-contained, so the set is duplicated here on purpose.
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".forge",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".hypothesis",
    }
)

# A directory counts as a workspace member when it carries any of these —
# ``.git`` or a project manifest: gitless directories are first-class.
MEMBER_MARKERS = frozenset(
    {
        ".git",
        "forge.json",
        "forge.agentic.json",
        "pyproject.toml",
        "package.json",
    }
)


def _is_project_root(d: Path) -> bool:
    return any(os.path.lexists(d / marker) for marker in MEMBER_MARKERS)


def _is_plain_dir(entry: os.DirEntry[str]) -> bool:
    """A real directory: never a symlink or a junction (not followed)."""
    try:
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            return False
    except OSError:
        return False
    isjunction = getattr(os.path, "isjunction", None)
    return not (isjunction is not None and isjunction(entry.path))


def discover_projects(root: Path, *, max_depth: int = 2) -> list[Path]:
    """Independent projects under a workspace root — directories carrying
    a ``MEMBER_MARKERS`` entry (``.git`` or a project manifest, so gitless
    members count). ``root`` itself is included when it is a project
    root; nested members are found up to ``max_depth`` levels deep. The
    walk never follows symlinks or junctions, skips hidden and
    ``IGNORED_DIRS`` directories, and never descends into a discovered
    member (its inner modules stay isolated)."""
    root = Path(root).resolve()
    found: list[Path] = []
    if _is_project_root(root):
        found.append(root)

    def _walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            with os.scandir(d) as it:
                children = sorted(
                    (entry for entry in it if _is_plain_dir(entry)),
                    key=lambda entry: entry.name,
                )
        except OSError:
            return
        for entry in children:
            name = entry.name
            if name.startswith(".") or name in IGNORED_DIRS:
                continue
            child = Path(entry.path)
            if _is_project_root(child):
                found.append(child.resolve())
            else:
                _walk(child, depth + 1)

    _walk(root, 1)
    return found


def member_precedence(member: Path, spec: ForgeSpec) -> str | None:
    """``"project"`` when a member already carries a project-scope install
    or its own managed MCP key for this forge — local specializes the
    workspace install and must never be silently widened. ``None`` when
    the member is a clean adoption target."""
    state = member / spec.state_dir
    if state.is_dir():
        return "project"
    mcp = member / ".mcp.json"
    if spec.mcp_server_name and mcp.exists():
        doc = _load_json(mcp, None)
        if isinstance(doc, dict) and spec.mcp_server_name in (doc.get("mcpServers") or {}):
            return "project"
    return None


# --------------------------------------------------------------------------
# Spawn-gated ops — refused honestly where subprocess is unavailable
# --------------------------------------------------------------------------


def _spawn(
    spec: ForgeSpec,
    cmd: list[str],
    *,
    timeout: int = 30,
    input_bytes: bytes | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, bytes, bytes]:
    if not spec.spawn_ok:
        raise InstallError(
            E_SPAWN,
            f"{spec.forge_id}: subprocess disabled by policy — "
            "run the equivalent scripts/ helper instead",
        )
    # --vendor-strip:subprocess-begin
    import subprocess  # noqa: PLC0415 - lazy: spawn_ok gates reachability

    proc = subprocess.run(  # noqa: S603 — cmd is the declared spec
        cmd, capture_output=True, timeout=timeout, input=input_bytes, env=env
    )
    return proc.returncode, proc.stdout, proc.stderr
    # --vendor-strip:subprocess-end


def mcp_verify(spec: ForgeSpec, *, timeout: int = 20) -> dict[str, Any]:
    """JSON-RPC stdio handshake: initialize → notifications/initialized →
    tools/list. Reports honestly — never infers success from file presence."""
    if not spec.mcp_server_name or not spec.mcp_command:
        return {
            "id": "mcp-handshake",
            "status": "NOT_APPLICABLE",
            "detail": "no MCP server declared",
        }
    if not spec.spawn_ok:
        return {
            "id": "mcp-handshake",
            "status": "BLOCKED",
            "detail": "subprocess disabled by policy",
        }
    exe = spec.mcp_command[0]
    resolved_exe = exe
    if not (os.sep in exe or (os.altsep and os.altsep in exe)):
        import shutil  # noqa: PLC0415

        resolved_exe = shutil.which(exe)
        if resolved_exe is None:
            return {
                "id": "mcp-handshake",
                "status": "BLOCKED",
                "detail": f"{exe!r} not resolvable on PATH — install incomplete",
            }
    spawn_command = list(spec.mcp_command)
    # Windows PATH launchers are commonly `.cmd`/`.bat` shims. `Popen` with
    # `shell=False` (the safe default) does not execute those shims when the
    # command is passed as an argv list, even though the host can launch them
    # interactively. Route only that wrapper through cmd.exe; real executables
    # keep the original no-shell path and every declared argument is preserved.
    if os.name == "nt" and resolved_exe:
        suffix = Path(resolved_exe).suffix.lower()
        if suffix in (".cmd", ".bat"):
            spawn_command = ["cmd.exe", "/d", "/c", resolved_exe, *spec.mcp_command[1:]]
        elif not (os.sep in exe or (os.altsep and os.altsep in exe)):
            spawn_command[0] = resolved_exe
    try:
        import subprocess  # noqa: PLC0415

        proc = subprocess.Popen(  # noqa: S603 — command is the declared spec
            spawn_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        # CLI not on PATH — the install is incomplete, not defective.
        return {
            "id": "mcp-handshake",
            "status": "BLOCKED",
            "detail": f"cannot spawn {spec.mcp_command[0]!r}: {exc}",
        }
    except OSError as exc:
        return {
            "id": "mcp-handshake",
            "status": "FAIL",
            "detail": f"cannot spawn {spec.mcp_command[0]!r}: {exc}",
        }
    if proc.stdin is None or proc.stdout is None:
        proc.kill()
        return {"id": "mcp-handshake", "status": "FAIL", "detail": "stdio pipes unavailable"}

    # A Windows ``.cmd`` launcher can leave the real provider holding the
    # wrapper's stderr handle after the wrapper exits.  Reading
    # ``proc.stderr`` only during cleanup would then block the Portal worker
    # forever (the MCP handshake itself may already have completed).  Drain
    # stderr in a daemon pump and keep only a bounded tail instead.
    stderr_tail = bytearray()

    def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        for chunk in iter(proc.stderr.readline, b""):
            stderr_tail.extend(chunk)
            del stderr_tail[:-400]

    threading.Thread(target=_drain_stderr, daemon=True, name="mcp-stderr").start()
    try:
        out = _mcp_handshake(proc, spec, timeout)
    except Exception as exc:  # handshake failures are FAIL, not crashes
        out = {"id": "mcp-handshake", "status": "FAIL", "detail": str(exc)}
    killed = False
    # Kill the exact process tree on Windows so a cmd.exe shim cannot leave
    # a specialist MCP child alive.  On other platforms the existing
    # terminate/kill path remains the portable fallback.
    if os.name == "nt" and proc.poll() is None:
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except Exception:  # noqa: S110 — best-effort tree cleanup
            pass
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


def _mcp_handshake(proc: Any, spec: ForgeSpec, timeout: int) -> dict[str, Any]:
    """initialize → initialized → tools/list (+ tools/call when the spec
    declares a no-arg ``mcp_verify_tool``). Returns the check dict; the
    caller attaches process-exit evidence."""
    assert proc.stdin is not None and proc.stdout is not None
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
    if spec.mcp_verify_tool:
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": spec.mcp_verify_tool, "arguments": {}},
                }
            ).encode()
            + b"\n"
        )
    proc.stdin.flush()
    # selectors cannot watch pipes on Windows — a daemon reader thread
    # draining stdout into a queue is the portable deadline mechanism.
    import queue  # noqa: PLC0415
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    lines: "queue.Queue[bytes]" = queue.Queue()

    def _drain() -> None:
        for ln in proc.stdout:
            lines.put(ln)

    threading.Thread(target=_drain, daemon=True).start()
    tools: list[str] = []
    got_init = False
    invoke: dict[str, Any] | None = (
        {"tool": spec.mcp_verify_tool, "status": "UNVERIFIED", "detail": "no response"}
        if spec.mcp_verify_tool
        else None
    )
    want_ids = {1, 2} | ({3} if spec.mcp_verify_tool else set())
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
        elif mid == 3 and invoke is not None:
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
    out: dict[str, Any] = {
        "id": "mcp-handshake",
        "status": "PASS",
        "detail": f"{len(tools)} tools",
        "tools": tools[:50],
    }
    if invoke is not None:
        out["invoke"] = invoke
    return out


def cli_version(spec: ForgeSpec, *, timeout: int = 15) -> str | None:
    try:
        code, out, _ = _spawn(spec, [spec.cli_name, *spec.version_cmd], timeout=timeout)
        if code == 0:
            return out.decode(errors="replace").strip().splitlines()[0]
    except (InstallError, Exception):
        return None
    return None


# --------------------------------------------------------------------------
# Launcher shims — make <cli> resolvable in a new terminal
# --------------------------------------------------------------------------


def write_launcher(spec: ForgeSpec, venv: Path) -> Path:
    """Write a shim into the shim dir that delegates to the venv's console
    script. POSIX gets an exec wrapper; Windows gets a .cmd + a bash shim."""
    bindir = _venv_bin(venv)
    real = bindir / (spec.cli_name + (".exe" if os.name == "nt" else ""))
    if not real.exists():
        real = bindir / spec.cli_name
    shim_dir = _shim_dir()
    shim_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        cmd = shim_dir / f"{spec.cli_name}.cmd"
        cmd.write_text(f'@echo off\r\n"{real}" %*\r\n', encoding="utf-8")
        bash = shim_dir / spec.cli_name
        bash.write_text(f'#!/bin/sh\nexec "{real}" "$@"\n', encoding="utf-8")
        return cmd
    shim = shim_dir / spec.cli_name
    shim.write_text(f'#!/bin/sh\nexec "{real}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return shim


def path_guidance() -> str | None:
    """None when the shim dir is already on PATH; else the line to add."""
    shim = _shim_dir()
    path = os.environ.get("PATH", "")
    if str(shim) in path.split(os.pathsep):
        return None
    if os.name == "nt":
        return f'setx PATH "%PATH%;{shim}"  # then open a new terminal'
    shell = Path(os.environ.get("SHELL", "/bin/sh")).name
    rc = {"zsh": "~/.zshrc", "bash": "~/.bashrc"}.get(shell, "~/.profile")
    return f"echo 'export PATH=\"{shim}:$PATH\"' >> {rc}  # then restart the shell"


__all__ = [name for name in dir() if not name.startswith("_")]
