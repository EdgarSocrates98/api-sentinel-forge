"""Workspace resolution — project (.api-sentinel/) or user (~/.api-sentinel/)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from api_sentinel.domain import WorkspaceError

Scope = Literal["project", "user"]


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
