"""C101-F005 — real Forge Lens -> Sentinel refs correlation chain.

Proves `Sentinel Incident -> Bridge Session -> Lens Trace -> Evidence
Reference` end-to-end against a *real* forge-lens executable:

1. `forge-lens init` a throwaway project.
2. `forge-lens ingest` a real TraceIngest document whose `session_id`
   matches a Bridge session id (`ses_bridge_...`).
3. `lab/lens_export_refs.py` runs `forge-lens traces list --json` and
   emits the refs manifest the Sentinel consumes.
4. `FileLensACL` resolves refs and the bridge session id correlates to
   the stored trace — references only, never payload copies.

Skipped (never failed) when the sibling forge-lens venv is absent —
an unavailable optional integration is a named state, not a failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from api_sentinel.integrations.errors import IntegrationUnavailable
from api_sentinel.integrations.lens.adapter import (
    FileLensACL,
    UnavailableLensACL,
    lens_from_config,
)

_REPO = Path(__file__).resolve().parents[2]
_LENS_REPO = _REPO.parent / "forge-lens"
_LENS_EXE = _LENS_REPO / ".venv" / "Scripts" / (
    "forge-lens.exe" if os.name == "nt" else "forge-lens"
)
_EXPORTER = _REPO / "lab" / "lens_export_refs.py"
_FIXTURE = _LENS_REPO / "tests" / "fixtures" / "native-ingest.json"
_BRIDGE_SESSION = "ses_bridge_cycle101"

pytestmark = pytest.mark.skipif(
    not (_LENS_EXE.is_file() and _FIXTURE.is_file()),
    reason="forge-lens executable/fixture absent — optional integration",
)


def _lens(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed local executable + argv
        [str(_LENS_EXE), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_lens_trace_correlation_real_chain(tmp_path: Path) -> None:
    project_dir = tmp_path / "lens-project"
    project_dir.mkdir()
    init = _lens("init", cwd=project_dir)
    assert init.returncode == 0, init.stderr

    # Ingest the real fixture patched to carry the bridge session id.
    doc = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    doc["traces"][0]["session_id"] = _BRIDGE_SESSION
    ingest_file = tmp_path / "ingest.json"
    ingest_file.write_text(json.dumps(doc), encoding="utf-8")
    ing = _lens("ingest", str(ingest_file), cwd=project_dir)
    assert ing.returncode == 0, ing.stderr
    assert "1 trace(s)" in ing.stdout

    # Lens stores under the *envelope* project_id, not the trace-level one.
    project_id = str(doc.get("project_id") or "default")
    probe = _lens(
        "traces", "list", "--json", "-n", "50",
        "--project", project_id,
        cwd=project_dir,
    )
    assert '"trace_id"' in probe.stdout, probe.stdout + probe.stderr

    # Export refs through the real `traces list --json` path.
    manifest = tmp_path / "refs.manifest.json"
    ex = subprocess.run(  # noqa: S603 - fixed argv list
        [
            sys.executable,
            str(_EXPORTER),
            "--lens-exe",
            str(_LENS_EXE),
            "--project-dir",
            str(project_dir),
            "--project",
            project_id,
            "--out",
            str(manifest),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert ex.returncode == 0, ex.stderr

    # Sentinel side: FileLensACL resolves the reference — never payloads.
    acl = FileLensACL(manifest)
    result = acl.trace_refs()
    assert result.status == "available", manifest.read_text(encoding="utf-8")
    by_session = {r.session_id: r for r in result.refs if r.session_id}
    assert _BRIDGE_SESSION in by_session, (
        manifest.read_text(encoding="utf-8") + f" :: {result.refs}"
    )
    ref = by_session[_BRIDGE_SESSION]
    assert ref.trace_id == "cccccccccccccccccccccccccccccccc"
    assert ref.provenance == "observed"
    # Allowlist: no span/prompt keys may leak into the manifest.
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    assert all(
        set(r) <= {"trace_id", "session_id", "service_id", "at", "provenance"}
        for r in raw["refs"]
    )


def test_lens_unavailable_is_named_state(tmp_path: Path) -> None:
    missing = FileLensACL(tmp_path / "absent.json").trace_refs()
    assert missing.status == "unavailable"
    assert missing.refs == []
    absent_transport = UnavailableLensACL().trace_refs()
    assert absent_transport.status == "unavailable"
    with pytest.raises(IntegrationUnavailable):
        # configured-but-absent manifest is an explicit contract failure
        lens_from_config(
            {"integrations": {"lens": {"refs": "missing.json"}}}, tmp_path
        )
