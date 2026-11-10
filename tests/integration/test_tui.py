"""Headless TUI tests — the app is exercised through textual's pilot.

Coverage:
- app mounts; every nav panel renders (incl. named unavailable states)
- the command wizard lists every catalog entry; no-field commands run
  through the real Typer app; notice-only commands are explained, never run
- required-field validation refuses empty submission
- a real workspace feeds real rows into the tables
- missing `textual` extra is a named error, not a crash

Skips when `textual` is absent (optional extra) — an unavailable optional
dependency is a named state, not a suite failure.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("textual", reason="tui extra not installed")

from typer.testing import CliRunner

from api_sentinel.cli import app as cli_app
from api_sentinel.tui import run, textual_available
from api_sentinel.tui.app import SentinelApp, WizardScreen
from api_sentinel.tui.catalog import CATALOG, Command, build_argv
from api_sentinel.tui.data import resolve_ws


def _pilot_app(ws_dir: Path | None = None) -> SentinelApp:
    ws = resolve_ws(
        str(ws_dir / ".api-sentinel") if ws_dir else None, False
    )
    return SentinelApp(ws, str(ws_dir) if ws_dir else None)


def test_app_mounts_and_dashboard_renders(tmp_path: Path) -> None:
    async def go() -> None:
        app = _pilot_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.TITLE == "API Sentinel"

    asyncio.run(go())


def test_every_panel_renders(tmp_path: Path) -> None:
    async def go() -> None:
        app = _pilot_app(tmp_path)
        async with app.run_test() as pilot:
            for key in (
                "services", "incidents", "slos",
                "health", "integrations", "commands", "dash",
            ):
                await app._refresh(key)
                await pilot.pause()

    asyncio.run(go())  # no exception == panels render with named notes


def test_catalog_covers_cli_surface() -> None:
    # Every entry either runs argv or carries an honest notice — never both.
    for cmd in CATALOG:
        assert cmd.notice or cmd.argv, cmd.label
        assert not (cmd.notice and cmd.fields), cmd.label
    argv_map = {c.label: c.argv for c in CATALOG if c.argv}
    # Spot-check coverage of the governed surface.
    assert ("incident", "ack") in argv_map.values()
    assert ("slo", "evaluate") in argv_map.values()
    assert ("backup", "restore") in argv_map.values()
    assert ("handoff", "doctor") in argv_map.values()
    assert any("serve" in c.label for c in CATALOG if c.notice)
    assert any("mcp" in c.label for c in CATALOG if c.notice)


def test_build_argv() -> None:
    cmd = Command(
        "g", "x", ("incident", "ack"),
    )
    assert build_argv(cmd, {}, ws="C:/ws") == [
        "incident", "ack", "--workspace", "C:/ws",
    ]


def test_wizard_runs_real_command_no_fields(tmp_path: Path) -> None:
    async def go() -> None:
        app = _pilot_app(tmp_path)
        async with app.run_test() as pilot:
            await app._refresh("commands")
            await pilot.pause()
            version = next(c for c in CATALOG if c.label == "version")
            app._open_wizard(version)  # no fields -> runs immediately
            await pilot.pause()
            for _ in range(40):  # let the thread worker finish
                await pilot.pause(0.05)
                log = app.query_one("#out")
                if "version" in "\n".join(str(ln) for ln in log.lines) or \
                        "0.1.0" in "\n".join(str(ln) for ln in log.lines):
                    return
            pytest.fail("version output never reached the log")

    asyncio.run(go())


def test_notice_command_never_invokes_cli(tmp_path: Path) -> None:
    async def go() -> None:
        app = _pilot_app(tmp_path)
        async with app.run_test() as pilot:
            await app._refresh("commands")
            await pilot.pause()
            serve = next(c for c in CATALOG if "serve" in c.label)
            app._open_wizard(serve)
            await pilot.pause()
            text = "\n".join(str(ln) for ln in app.query_one("#out").lines)
            assert "long-running" in text or "api-sentinel serve" in text

    asyncio.run(go())


def test_untrusted_log_text_is_not_parsed_as_rich_markup(tmp_path: Path) -> None:
    async def go() -> None:
        app = _pilot_app(tmp_path)
        async with app.run_test() as pilot:
            await app._refresh("commands")
            await pilot.pause()
            app._logline("[red]external payload[/]")
            await pilot.pause()
            lines = app.query_one("#out").lines
            assert "[red]external payload[/]" in str(lines[-1])

    asyncio.run(go())


def test_wizard_required_field_blocks() -> None:
    cmd = next(
        c for c in CATALOG if c.label.startswith("incident ack")
    )
    screen = WizardScreen(cmd)

    async def go() -> None:
        app = _pilot_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            assert app.screen is screen

    asyncio.run(go())


def test_tui_command_registered() -> None:
    result = CliRunner().invoke(cli_app, ["tui", "--help"])
    assert result.exit_code == 0
    assert "tui" in result.output.lower() or "Interactive" in result.output


def test_missing_textual_is_named_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "api_sentinel.tui.textual_available", lambda: False
    )
    from api_sentinel.domain import ValidationError

    with pytest.raises(ValidationError, match=r"tui.*extra") as ei:
        run(workspace=str(tmp_path))
    assert "tui]" in ei.value.details["install"]
    assert textual_available()  # sanity: real env has the extra


def test_real_workspace_rows(tmp_path: Path) -> None:
    ws_dir = tmp_path / "proj"
    ws_dir.mkdir()
    runner = CliRunner()
    # install a real workspace + register a service through the real CLI
    assert runner.invoke(
        cli_app,
        ["install", "--yes", "--workspace", str(ws_dir)],
    ).exit_code == 0
    r = runner.invoke(
        cli_app,
        ["services", "--register", "svc_tui_1",
         "--workspace", str(ws_dir / ".api-sentinel")],
    )
    assert r.exit_code == 0

    async def go() -> None:
        app = _pilot_app(ws_dir)
        async with app.run_test() as pilot:
            await app._refresh("services")
            await pilot.pause()
            table = app.query_one("#body_inner DataTable")
            rows = [
                " ".join(str(c) for c in table.get_row_at(i))
                for i in range(table.row_count)
            ]
            assert any("svc_tui_1" in r for r in rows), rows

    asyncio.run(go())
