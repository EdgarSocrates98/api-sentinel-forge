"""Portable bootstrap installer for API Sentinel Forge.

Thin shim kept dependency-free (stdlib only): it creates or locates a
Python 3.11+ interpreter, installs the package editable when running from
a checkout, then delegates to ``api-sentinel install`` for the guided,
governed part of the setup. The heavy UX lives in the CLI, not here.

Channels: this shim produces no machine contract on stdout — each typed
FORGE-* refusal goes to stderr as a greppable line, and the decorated
panel/banner appear on stderr when the terminal supports them (NO_COLOR /
FORGE_NO_UI / CI / GITHUB_ACTIONS / TERM=dumb / --quiet / --plain gate it).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

MIN_VERSION = (3, 11)
NAME = "API Sentinel Forge"
CLI = "api-sentinel"

_REMEDIES = {
    "FORGE-PYTHON-INCOMPATIBLE": (
        f"install Python {MIN_VERSION[0]}.{MIN_VERSION[1]}+ and reopen the shell"
    ),
    "FORGE-INSTALL-NO-CHECKOUT": "run this installer from the api-sentinel-forge checkout root",
    "FORGE-INSTALL-RUNTIME-FAILED": "see the pip output above — the step log is the evidence",
    "FORGE-INSTALL-DELEGATE-FAILED": (
        "the CLI installed but `api-sentinel install` failed — run it once to see the real error"
    ),
}


def _vt() -> None:
    """Best-effort ENABLE_VIRTUAL_TERMINAL_PROCESSING for legacy conhost."""
    if os.name != "nt":
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        for fd in (-11, -12):
            handle = k32.GetStdHandle(fd)
            mode = ctypes.c_ulong()
            if k32.GetConsoleMode(handle, ctypes.byref(mode)):
                k32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # noqa: S110  # pragma: no cover — best effort on exotic hosts
        pass


def _ui_enabled(quiet: bool, plain: bool) -> bool:
    if quiet or plain:
        return False
    forced = os.environ.get("FORGE_BOOTSTRAP_UI", "").strip().lower()
    if forced in ("always", "on", "rich", "1", "yes", "true"):
        return True
    if forced in ("never", "off", "plain", "0", "no", "false"):
        return False
    if os.environ.get("NO_COLOR") is not None or os.environ.get("FORGE_NO_UI"):
        return False
    if (
        os.environ.get("TERM", "") == "dumb"
        or os.environ.get("CI")
        or os.environ.get("GITHUB_ACTIONS")
    ):
        return False
    return sys.stderr.isatty()


class _Ux:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        enc = (getattr(sys.stderr, "encoding", None) or "").lower()
        uni = "utf" in enc or os.environ.get("WT_SESSION") or os.environ.get("MSYSTEM")
        uni_g = {"ok": "✓", "fail": "✗", "plan": "◌", "em": "◆",
                 "arrow": "→", "rule": "─", "bullet": "·"}
        asc_g = {"ok": "OK", "fail": "x", "plan": "o", "em": "*",
                 "arrow": "->", "rule": "-", "bullet": "-"}
        self._g = uni_g if uni else asc_g
        if enabled:
            _vt()

    def _c(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m"

    def _w(self, text: str = "") -> None:
        sys.stderr.write(text + "\n")
        sys.stderr.flush()

    def banner(self, editable: bool) -> None:
        if not self.enabled:
            return
        g, c = self._g, self._c
        mode = "editable checkout" if editable else "installed cli"
        head = f"portable install {g['bullet']} {mode} {g['bullet']} delegates to `{CLI} install`"
        self._w("")
        self._w(f"  {c('38;5;208;1', g['em'] + ' FORJAS')} {c('2', g['bullet'])} {c('1', NAME)}")
        self._w(f"  {c('2', head)}")
        self._w(f"  {c('2', g['rule'] * 54)}")

    def step(self, i: int, n: int, label: str) -> None:
        if self.enabled:
            g, c = self._g, self._c
            self._w(f"  {c('38;5;208', g['bullet'])} {c('2', f'[{i}/{n}]')} {label}")

    def done(self, i: int, n: int, label: str, note: str = "") -> None:
        if self.enabled:
            g, c = self._g, self._c
            self._w(f"  {c('32', g['ok'])} {c('2', f'[{i}/{n}]')} {label}")
            if note:
                self._w(f"      {c('2', note)}")

    def cmdline(self, cmd: list[str]) -> None:
        if self.enabled:
            self._w(f"      {self._c('2', '$ ' + ' '.join(cmd))}")

    def refuse(self, kind: str, detail: str) -> None:
        if not self.enabled:
            self._w(f"{kind}: {detail}")
            return
        g, c = self._g, self._c
        self._w("")
        head = f"{c('31', g['fail'])} {c('1;31', 'install refused')} "
        self._w(f"  {head}{c('2', g['rule'])} {c('31', kind)}")
        self._w(f"    {c('2', detail)}")
        remedy = _REMEDIES.get(kind)
        if remedy:
            self._w(f"    {c('33', 'remedy')}  {remedy}")
        self._w("")

    def summary(self, hint: str) -> None:
        if not self.enabled:
            return
        g, c = self._g, self._c
        self._w(f"  {c('2', g['rule'] * 54)}")
        tail = g["rule"] + " guided setup follows"
        self._w(f"  {c('32', g['ok'])} {c('1', 'bootstrap complete')} {c('2', tail)}")
        self._w("")
        self._w(f"    {c('33', 'next')}  {hint}")
        self._w(f"  {c('2', g['rule'] * 54)}")
        self._w("")


def _run(cmd: list[str], ui: _Ux) -> int:
    ui.cmdline(cmd)
    return subprocess.call(cmd)  # noqa: S603 — argv list built from fixed values below


def main() -> int:
    parser = argparse.ArgumentParser(description="API Sentinel Forge portable installer")
    parser.add_argument(
        "--no-editable",
        action="store_true",
        help="Skip editable install of the local checkout (assumes api-sentinel already installed)",
    )
    parser.add_argument("--yes", action="store_true", help="Accept defaults (non-interactive)")
    parser.add_argument("--quiet", action="store_true", help="Suppress decoration")
    parser.add_argument("--plain", action="store_true", help="Disable ANSI styling")
    args, passthrough = parser.parse_known_args()

    ui = _Ux(_ui_enabled(args.quiet, args.plain))

    def refuse(kind: str, detail: str, code: int = 2) -> int:
        ui.refuse(kind, detail)
        return code

    if sys.version_info < MIN_VERSION:
        return refuse(
            "FORGE-PYTHON-INCOMPATIBLE",
            f"api-sentinel-forge requires Python {MIN_VERSION[0]}.{MIN_VERSION[1]}+; "
            f"found {sys.version.split()[0]}",
        )

    checkout = Path(__file__).resolve().parent.parent
    if not (checkout / "pyproject.toml").is_file():
        return refuse("FORGE-INSTALL-NO-CHECKOUT", f"pyproject.toml not found under {checkout}")

    ui.banner(not args.no_editable)
    n = 1 if args.no_editable else 2

    if not args.no_editable:
        ui.step(1, n, "install checkout editable (pip -e)")
        pip_cmd = [sys.executable, "-m", "pip", "install", "-e", str(checkout)]
        code = _run(pip_cmd, ui)
        if code != 0:
            return refuse(
                "FORGE-INSTALL-RUNTIME-FAILED",
                f"pip install -e exited {code}",
                code=code,
            )
        ui.done(1, n, "editable install")

    i = n
    ui.step(i, n, f"delegate to `{CLI} install` (guided setup)")
    sentinel_args = ["api-sentinel", "install"]
    if args.yes:
        sentinel_args.append("--yes")
    sentinel_args.extend(passthrough)
    code = _run(sentinel_args, ui)
    if code != 0:
        return refuse("FORGE-INSTALL-DELEGATE-FAILED", f"`{CLI} install` exited {code}", code=code)
    ui.done(i, n, "guided setup")

    ui.summary(f"run `{CLI} doctor` to verify the environment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
