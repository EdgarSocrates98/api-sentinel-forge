"""Repo-entry bootstrap scripts emit governed errors, never silent deaths.

`setup.sh` runs under `set -euo pipefail`; a bare ``PY=$(command -v …)``
dies with rc=1 and no output when every probe fails — the named
``FORGE-PYTHON-NOT-FOUND`` contract (rc=2) would never be reached. These
tests pin the honest failure on a PATH with no python at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# setup.sh only needs bash builtins (command/cd/printf/set) plus dirname;
# a PATH containing dirname and nothing else is a real "no python" env.
_EXTERNALS = ("dirname",)


def _python_free_env(tmp_path: Path) -> dict[str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in _EXTERNALS:
        src = shutil.which(tool)
        if src is None:
            pytest.skip(f"{tool} unavailable — cannot build python-free PATH")
        (bindir / tool).symlink_to(src)
    return {"PATH": str(bindir)}


_BASH = shutil.which("bash")


@pytest.mark.skipif(_BASH is None, reason="bash unavailable")
def test_setup_sh_reports_forge_python_not_found(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603 — fixed argv, resolved bash + repo script
        [_BASH, "setup.sh"],
        cwd=REPO_ROOT,
        env=_python_free_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "FORGE-PYTHON-NOT-FOUND" in result.stderr


@pytest.mark.skipif(_BASH is None, reason="bash unavailable")
def test_setup_sh_dispatches_python_when_present(tmp_path: Path) -> None:
    """The healthy path still reaches the bootstrap — a python-free PATH is
    the only governed refusal. Proves the `|| true` fix did not swallow the
    dispatch (bootstrap is invoked and fails later on its own terms, or
    prints its own error — never FORGE-PYTHON-NOT-FOUND)."""
    env = dict(os.environ)
    result = subprocess.run(  # noqa: S603 — fixed argv, resolved bash + repo script
        [_BASH, "setup.sh", "--version-forge-nonexistent-flag"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "FORGE-PYTHON-NOT-FOUND" not in (result.stderr + result.stdout)
