"""CLI sweep — every wizard-catalog command exercised against a real workspace.

The TUI's command coverage is only honest if every catalog entry maps to
a real CLI invocation. This suite provisions a real workspace (install →
register service/source → define SLO → detect) and runs EVERY catalog
command through `CliRunner`.

Oracle: every command terminates with a *governed* outcome — exit code
0, or a named SentinelError/typer usage error. An uncaught crash
(TypeError/AttributeError/KeyError/traceback) is a defect, never an
acceptable failure mode.
"""

from __future__ import annotations

import json

import pytest
from typer import Exit as TyperExit
from typer.testing import CliRunner

from api_sentinel.cli import app as cli_app
from api_sentinel.domain import SentinelError
from api_sentinel.tui.catalog import CATALOG, Command, build_argv

runner = CliRunner()

# Commands that are honest *notices* in the TUI are skipped here; commands
# whose required inputs can't exist in a fresh workspace are run anyway —
# a named refusal/not-found is the expected governed outcome.
_FIELD_VALUES: dict[str, str] = {
    "incident_id": "inc_nonexistent",
    "--service-id": "PLACEHOLDER_SERVICE",
    "--incident-id": "inc_nonexistent",
    "--slo-id": "PLACEHOLDER_SLO",
    "--hypothesis-id": "hyp_nonexistent",
    "--verification-id": "ver_nonexistent",
    "--target": "0.99",
    "--window": "15m",
    "--window-start": "2026-01-01T00:00:00+00:00",
    "--window-end": "2026-01-01T00:15:00+00:00",
    "--at": "2026-01-01T00:00:00+00:00",
    "--file": "nonexistent.json",
    "--bundle": "nonexistent.json",
    "--response": "nonexistent.json",
    "--from": "nonexistent-backup-dir",
    "--kind": "change",
    "--suggested-change": "sweep change",
    "--reason": "sweep reason",
    "--text": "sweep hypothesis",
    "--register": "",
    "--principal": "sweep",
    "--actor": "sweep",
    "--profile": "local",
    "--scope": "project",
    "--environment": "local",
}


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """Provision a real workspace; return resolved entity ids."""
    ws_dir = tmp_path_factory.mktemp("sweep") / "proj"
    ws_dir.mkdir(parents=True)
    ws = str(ws_dir / ".api-sentinel")

    def run(*argv: str):
        return runner.invoke(cli_app, [*argv, "--workspace", ws, "--json"])

    assert run("install", "--yes").exit_code == 0
    r = run("services", "--register", "sweep-svc")
    assert r.exit_code == 0
    service_id = json.loads(r.output)["service_id"]
    r = run(
        "sources", "--register", "sweep-collector",
        "--service-id", service_id,
    )
    assert r.exit_code == 0
    source_id = json.loads(r.output)["source_id"]
    r = run(
        "slo", "define", "--service-id", service_id,
        "--target", "0.99", "--window", "15m",
    )
    assert r.exit_code == 0
    slo_id = json.loads(r.output)["slo_id"]
    run("detect", "--window-start", "2026-01-01T00:00:00+00:00",
        "--window-end", "2026-01-01T00:15:00+00:00")
    return {
        "ws": ws,
        "service_id": service_id,
        "source_id": source_id,
        "slo_id": slo_id,
    }


def _values(cmd: Command, seeded: dict[str, str]) -> dict[str, str]:
    vals: dict[str, str] = {}
    for f in cmd.fields:
        if f.kind == "bool":
            continue  # never apply destructive flags in the sweep
        v = _FIELD_VALUES.get(f.flag, f.default)
        if f.flag == "--service-id" and "register" not in cmd.label:
            v = seeded["service_id"]
        elif f.flag == "--slo-id":
            v = seeded["slo_id"]
        elif f.flag == "--source-id":
            v = seeded["source_id"]
        elif f.flag == "--register" and "sources" in cmd.label:
            v = "sweep-src-2"
        elif f.flag == "--register" and "services" in cmd.label:
            v = "sweep-svc-2"
        vals[f.flag] = v
    return vals


_GOVERNED_EXC = (SentinelError, SystemExit, TyperExit)


@pytest.mark.parametrize("cmd", CATALOG, ids=lambda c: c.label)
def test_catalog_command_governed_outcome(
    cmd: Command, seeded: dict[str, str]
) -> None:
    if cmd.notice:
        pytest.skip("honest notice-only entry — not a CLI invocation")
    if cmd.argv == ("install",):
        pytest.skip("install is provisioned by the fixture")
    argv = build_argv(cmd, _values(cmd, seeded), ws=seeded["ws"])
    result = runner.invoke(cli_app, argv)
    exc = result.exception
    if exc is not None:
        assert isinstance(exc, _GOVERNED_EXC), (
            f"{cmd.label}: ungoverned crash {type(exc).__name__}: {exc}\n"
            f"argv={argv}"
        )
    assert result.exit_code in (0, 1, 2), (
        f"{cmd.label}: unexpected exit {result.exit_code}\nargv={argv}\n"
        f"out={result.output[-400:]}"
    )


def test_sweep_coverage_is_the_full_catalog() -> None:
    # The catalog must cover the governed surface: every top-level command
    # group plus the lifecycle subcommands.
    flat = {c.argv for c in CATALOG}
    for expected in (
        ("doctor",), ("status",), ("capabilities",), ("services",),
        ("sources",), ("ingest",), ("deps",), ("correlate",), ("detect",),
        ("incidents",), ("incident", "show"), ("incident", "ack"),
        ("incident", "resolve"), ("incident", "reopen"), ("health",),
        ("investigate",), ("integrations",), ("retention",),
        ("slo", "define"), ("slo", "evaluate"), ("slo", "list"),
        ("handoff", "doctor"), ("handoff", "ingest"),
        ("handoff", "apiforge"), ("handoff", "apiforge-ingest"),
        ("changes", "list"), ("changes", "record"), ("changes", "import"),
        ("changes", "correlate"), ("hypotheses", "add"),
        ("hypotheses", "promote"), ("hypotheses", "demote"),
        ("hypotheses", "verify"), ("aws", "collect"), ("aws", "discover"),
        ("backup", "create"), ("backup", "restore"), ("version",),
        ("workspace",), ("install",),
    ):
        assert expected in flat, f"catalog missing {expected}"
