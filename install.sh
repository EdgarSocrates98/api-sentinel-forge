#!/usr/bin/env bash
# API Sentinel Forge portable install — Python >=3.11 route.
# Delegates to scripts/install_portable.py which owns the pipeline;
# this wrapper only picks the interpreter and styles its own refusal.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

# -- decoration (plain when not interactive; honors NO_COLOR/CI) -----------
_f_yellow=""; _f_red=""; _f_reset=""
if [ -t 2 ] \
    && [ -z "${NO_COLOR:-}" ] && [ -z "${FORGE_NO_UI:-}" ] \
    && [ -z "${CI:-}" ] && [ -z "${GITHUB_ACTIONS:-}" ] \
    && [ "${TERM:-dumb}" != "dumb" ]; then
  _f_yellow=$'\033[33m'; _f_red=$'\033[31m'; _f_reset=$'\033[0m'
fi
_m_fail="x"; _m_dash="-"
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
  *[Uu][Tt][Ff]*|*[Uu][Tt][Ff]8*) _m_fail="✗"; _m_dash="—" ;;
esac

for candidate in python3 python py; do
  if command -v "$candidate" >/dev/null 2>&1; then
    exec "$(command -v "$candidate")" "$root/scripts/install_portable.py" "$@"
  fi
done
printf '  %s%s%s install: FORGE-PYTHON-NOT-FOUND %s Python 3.11+ not found on PATH\n' \
    "$_f_red" "$_m_fail" "$_f_reset" "$_m_dash" >&2
printf '    %sremedy:%s install Python 3.11+ and reopen the shell\n' \
    "$_f_yellow" "$_f_reset" >&2
exit 2
