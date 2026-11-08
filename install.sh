#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
for candidate in python3 python py; do
  if command -v "$candidate" >/dev/null 2>&1; then
    exec "$(command -v "$candidate")" "$root/scripts/install_portable.py" "$@"
  fi
done
echo "API Sentinel Forge: Python 3.11+ not found on PATH" >&2
exit 2
