$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Get-Command python3, python, py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $python) { throw "API Sentinel Forge: Python 3.11+ not found on PATH" }
$pythonArgs = @()
if ([IO.Path]::GetFileName($python.Source) -in @("py.exe", "py")) { $pythonArgs = @("-3") }
& $python.Source @pythonArgs (Join-Path $root "scripts\install_portable.py") @args
exit $LASTEXITCODE
