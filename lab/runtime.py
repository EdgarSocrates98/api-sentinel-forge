# ruff: noqa: S603 -- executable validation intentionally invokes a candidate
"""Small, cross-platform runtime resolution for the federated lab.

The lab is intentionally not a process framework.  This module only resolves
validated executables and paths so callers can keep using argv lists and the
existing process lifecycle.  Every result is either an existing executable or
an explicit missing-path sentinel for optional integrations.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


class RuntimeResolutionError(RuntimeError):
    """A required executable could not be resolved safely."""


def _unique(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _validated_python(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        result = subprocess.run(
            [str(path), "-c", "import sys; print(sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip().startswith("(")


def resolve_python(
    root: Path,
    *,
    venv: Path | None = None,
    explicit: str | Path | None = None,
    env_name: str = "FORGE_BOOT_PYTHON",
) -> Path:
    """Resolve a usable Python executable on Windows, Linux, or macOS."""
    configured = explicit or os.environ.get(env_name)
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    if venv is not None:
        candidates.extend(
            [
                venv / "Scripts" / "python.exe",
                venv / "Scripts" / "python",
                venv / "bin" / "python",
                venv / "bin" / "python3",
            ]
        )
    candidates.extend(
        [
            Path(sys.executable),
            root / ".venv" / "Scripts" / "python.exe",
            root / ".venv" / "Scripts" / "python",
            root / ".venv" / "bin" / "python",
            root / ".venv" / "bin" / "python3",
        ]
    )
    for candidate in _unique(candidates):
        if _validated_python(candidate):
            return candidate
    raise RuntimeResolutionError(
        "no validated Python executable found; checked: "
        + ", ".join(str(p) for p in _unique(candidates))
    )


def _entrypoint_candidates(python: Path, name: str) -> list[Path]:
    parent = python.parent
    return [
        parent / name,
        parent / f"{name}.exe",
        parent.parent / "bin" / name,
        parent.parent / "Scripts" / name,
        parent.parent / "Scripts" / f"{name}.exe",
    ]


def resolve_entrypoint(
    name: str,
    *,
    python: Path | None = None,
    explicit: str | Path | None = None,
    env_name: str | None = None,
    optional: bool = False,
) -> Path:
    """Resolve an installed console entry point without assuming ``.exe``."""
    configured = explicit or (os.environ.get(env_name) if env_name else None)
    candidates: list[Path] = [Path(configured)] if configured else []
    if python is not None:
        candidates.extend(_entrypoint_candidates(python, name))
    discovered = shutil.which(name)
    if discovered:
        candidates.append(Path(discovered))
    for candidate in _unique(candidates):
        if candidate.is_file():
            return candidate
    if optional:
        return Path(f"missing-optional-{name}")
    raise RuntimeResolutionError(
        f"required entry point {name!r} not found; checked: "
        + ", ".join(str(p) for p in _unique(candidates))
    )


def resolve_path(
    value: str | Path,
    *,
    root: Path | None = None,
    must_exist: bool = False,
    directory: bool = False,
) -> Path:
    """Resolve a configured path while preserving spaces and Unicode."""
    path = Path(value)
    if not path.is_absolute() and root is not None:
        path = root / path
    path = path.expanduser()
    if must_exist and not (path.is_dir() if directory else path.is_file()):
        raise RuntimeResolutionError(f"configured path is unavailable: {path}")
    return path
