"""Workspace resolution — project (.api-sentinel/) or user (~/.api-sentinel/)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from api_sentinel.domain import WorkspaceError

Scope = Literal["project", "workspace", "user"]

STATE_DIR_NAME = ".api-sentinel"


@dataclass(frozen=True)
class Workspace:
    root: Path
    scope: Scope

    @property
    def db_path(self) -> Path:
        return self.root / "sentinel.db"

    @property
    def config_path(self) -> Path:
        return self.root / "config.yaml"

    @property
    def blobs_dir(self) -> Path:
        return self.root / "blobs"

    @property
    def receipts_dir(self) -> Path:
        return self.root / "receipts"

    @property
    def handoffs_dir(self) -> Path:
        return self.root / "handoffs"

    @property
    def agent_sessions_dir(self) -> Path:
        """Governed session-request envelopes for the Agent Bridge (Cycle 8)."""
        return self.root / "agent_sessions"


def resolve_workspace(scope: Scope | None = None, explicit: Path | None = None) -> Workspace:
    """explicit path > explicit scope > API_SENTINEL_HOME > project > user."""
    if explicit is not None:
        root = explicit.expanduser().resolve()
        return Workspace(root=root, scope=scope or "project")
    env = os.environ.get("API_SENTINEL_HOME")
    if env:
        return Workspace(root=Path(env).expanduser().resolve(), scope=scope or "project")
    if scope == "user":
        return Workspace(root=Path.home() / ".api-sentinel", scope="user")
    return Workspace(root=Path.cwd() / ".api-sentinel", scope="project")


# --------------------------------------------------------------------------
# install-2.1 target resolution (Cycle 16 wave 11)
#
# Two different "roots" stay distinct here:
#
# - ``--root PATH``   the *target root* of the install — the project or
#                     workspace directory the Sentinel serves. The state
#                     dir is always ``<root>/.api-sentinel`` and the
#                     context registry root (``.forge/context``) is the
#                     target root itself. An explicit root is
#                     authoritative: discovery never substitutes an
#                     ancestor marker for it.
# - ``--workspace``   the *state dir* path, verbatim — the same flag every
#                     other command uses. It names the directory that
#                     holds ``sentinel.db``/``token``/``config.yaml``.
#
# ``context_root`` below is where ``.forge/context`` (and the consumer
# doc blocks) would be written; ``None`` means "no context registration
# is honest here" — user scope, a state dir not shaped like
# ``<root>/.api-sentinel``, or a root that resolves to $HOME/fs-root.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallTarget:
    """Resolved install destination + the roots each layer writes to."""

    ws: Workspace
    target_root: Path | None  # install target root (project/workspace) — None for user
    context_root: Path | None  # `.forge/context` owner — None = registration skipped
    source: str  # flag | env | marker | vcs | cwd | user-home | explicit-state
    notes: tuple[str, ...] = field(default_factory=tuple)


_WORKSPACE_MARKERS = (
    "forge.workspace.yaml",
    "forge.workspace.json",
    ".forge-workspace",
)


def _is_workspace_pyproject(root: Path) -> bool:
    try:
        txt = (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "tool.forge.workspace" in txt or "tool.uv.workspace" in txt


def _find_workspace_root(start: Path) -> Path | None:
    """Nearest ancestor carrying a workspace marker (installkit
    convention): ``forge.workspace.*`` manifests, a ``.forge-workspace``
    sentinel, a ``tool.forge.workspace``/``tool.uv.workspace`` table, or
    a VCS root — whichever is hit first walking up (bounded walk)."""
    cur = start.resolve()
    for _ in range(64):
        for name in _WORKSPACE_MARKERS:
            if (cur / name).exists():
                return cur
        if (cur / "pyproject.toml").is_file() and _is_workspace_pyproject(cur):
            return cur
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def _fs_root(path: Path) -> bool:
    return path.parent == path


def _safe_context_root(candidate: Path | None, notes: list[str]) -> Path | None:
    """Context registration never lands on the filesystem root or the
    user's home — that is the user-scope/account shape, not a project."""
    if candidate is None:
        return None
    if _fs_root(candidate):
        notes.append("context root resolves to the filesystem root — registration skipped")
        return None
    try:
        if candidate == Path.home().resolve():
            notes.append(
                "context root resolves to $HOME — that is the user-scope "
                "shape; registration skipped (use --scope user)"
            )
            return None
    except OSError:
        pass
    return candidate


def _context_root_for_state_dir(state_dir: Path, notes: list[str]) -> Path | None:
    """Context root implied by an explicit state dir — only when the dir
    is literally named ``.api-sentinel`` (the ``<root>/<state>`` shape);
    anything else cannot name its project root and skips honestly."""
    if state_dir.name == STATE_DIR_NAME:
        parent = state_dir.parent
        if parent != state_dir:
            return _safe_context_root(parent, notes)
        return None
    notes.append(
        f"state dir {state_dir.name!r} is not named {STATE_DIR_NAME!r} — "
        "no project root derivable; pass --root for context registration"
    )
    return None


def resolve_install_target(
    scope: Scope,
    *,
    root: Path | None = None,
    state_dir: Path | None = None,
) -> InstallTarget:
    """Resolve ``--root``/``--workspace``/``--scope`` into a Workspace +
    the target and context roots.

    Precedence: ``--workspace`` (verbatim state dir) > ``--root``
    (explicit target) > ``API_SENTINEL_HOME`` > scope discovery
    (workspace markers / VCS / cwd). ``--root`` + ``--workspace``
    together are refused — two explicit roots is an ambiguous request,
    never a guess.
    """
    notes: list[str] = []
    if root is not None and state_dir is not None:
        raise WorkspaceError(
            "conflicting targets: --root (target root) and --workspace "
            "(state dir) are alternatives — pass one, not both"
        )
    if state_dir is not None:
        sd = state_dir.expanduser().resolve()
        ctx = _context_root_for_state_dir(sd, notes)
        return InstallTarget(
            ws=Workspace(root=sd, scope=scope),
            target_root=ctx,
            context_root=ctx,
            source="explicit-state",
            notes=tuple(notes),
        )
    if scope == "user":
        if root is not None:
            notes.append(
                "--root ignored: 'user' scope always resolves to "
                "~/.api-sentinel (account level, no workspace context)"
            )
        return InstallTarget(
            ws=Workspace(root=Path.home() / STATE_DIR_NAME, scope="user"),
            target_root=None,
            context_root=None,
            source="user-home",
            notes=tuple(notes),
        )
    if root is not None:
        target = root.expanduser().resolve()
        if _fs_root(target):
            raise WorkspaceError("refused: --root resolves to the filesystem root")
        try:
            if target == Path.home().resolve():
                raise WorkspaceError(
                    "refused: --root resolves to $HOME — install with "
                    "--scope user for the account-level workspace"
                )
        except OSError:
            pass
        return InstallTarget(
            ws=Workspace(root=target / STATE_DIR_NAME, scope=scope),
            target_root=target,
            context_root=_safe_context_root(target, notes),
            source="flag",
            notes=tuple(notes),
        )
    env = os.environ.get("API_SENTINEL_HOME")
    if env:
        sd = Path(env).expanduser().resolve()
        ctx = _context_root_for_state_dir(sd, notes)
        return InstallTarget(
            ws=Workspace(root=sd, scope=scope),
            target_root=ctx,
            context_root=ctx,
            source="env",
            notes=tuple(notes),
        )
    if scope == "workspace":
        cwd = Path.cwd().resolve()
        found = _find_workspace_root(cwd) or cwd
        if found == cwd:
            notes.append(
                "no workspace marker or VCS root found above cwd — "
                "using cwd as the workspace root"
            )
        return InstallTarget(
            ws=Workspace(root=found / STATE_DIR_NAME, scope="workspace"),
            target_root=found,
            context_root=_safe_context_root(found, notes),
            source="marker" if found != cwd else "cwd",
            notes=tuple(notes),
        )
    # project default — same shape as resolve_workspace's project branch
    cwd = Path.cwd().resolve()
    return InstallTarget(
        ws=Workspace(root=cwd / STATE_DIR_NAME, scope="project"),
        target_root=cwd,
        context_root=_safe_context_root(cwd, notes),
        source="cwd",
        notes=tuple(notes),
    )


def ensure_workspace(ws: Workspace, *, profile: str = "local") -> Workspace:
    ws.root.mkdir(parents=True, exist_ok=True)
    ws.blobs_dir.mkdir(exist_ok=True)
    ws.receipts_dir.mkdir(exist_ok=True)
    ws.handoffs_dir.mkdir(exist_ok=True)
    ws.agent_sessions_dir.mkdir(exist_ok=True)
    if not ws.config_path.exists():
        ws.config_path.write_text(
            yaml.safe_dump({"scope": ws.scope, "profile": profile}, sort_keys=False),
            encoding="utf-8",
        )
    return ws


def load_config(ws: Workspace) -> dict[str, object]:
    if not ws.config_path.exists():
        raise WorkspaceError(f"workspace not initialized: {ws.root}")
    data = yaml.safe_load(ws.config_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
