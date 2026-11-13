#!/usr/bin/env bash
# Windows Git Bash adapter for scripts/install_portable.py, via install.sh.
# PowerShell users should run install.ps1; this route intentionally stays Bash-only.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "install.windows.sh: FORGE-SHELL-UNAVAILABLE — esta rota exige bash (Git Bash); a shell atual nao e bash" >&2
  echo "  remedy: run 'bash install.windows.sh' inside Git Bash, or use install.ps1 under PowerShell" >&2
  exit 2
fi
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$root/install.sh" "$@"
