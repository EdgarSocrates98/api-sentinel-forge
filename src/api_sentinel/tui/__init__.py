"""API Sentinel TUI — optional `textual`-backed interactive shell.

The extra is optional by design: `api-sentinel tui` without it is a
named error (`pip install "api-sentinel-forge[tui]"`), never a degraded
fake. Import nothing from `textual` at package import time — the guard
lives in `run()` so `--help` and the plain CLI stay dependency-free.
"""

from __future__ import annotations

from pathlib import Path

from api_sentinel.workspace import Workspace, resolve_workspace


def textual_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("textual") is not None


def run(
    workspace: str | None = None, user_scope: bool = False
) -> int:
    if not textual_available():
        from api_sentinel.domain import ValidationError

        raise ValidationError(
            "the 'tui' extra is required for `api-sentinel tui`",
            details={"install": 'pip install "api-sentinel-forge[tui]"'},
        )
    ws: Workspace = resolve_workspace(
        scope="user" if user_scope else "project",
        explicit=Path(workspace) if workspace else None,
    )
    from .app import run_tui

    return run_tui(ws, workspace)


__all__ = ["run", "textual_available"]
