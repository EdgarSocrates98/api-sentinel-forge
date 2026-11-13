# API Sentinel Forge portable install - delegates to scripts/install_portable.py.
# Wrapper styles only its own FORGE-* refusal via Write-Host colors.
$ErrorActionPreference = "Stop"

function Write-ForgeLine {
    param([string]$Mark, [string]$Color, [string]$Text)
    $plainOut = -not $Host.UI.SupportsVirtualTerminal -or $env:NO_COLOR -or $env:CI -or $env:FORGE_NO_UI
    if ($plainOut) { Write-Host $Text } else { Write-Host "  $Mark " -ForegroundColor $Color -NoNewline; Write-Host $Text }
}

$root = $PSScriptRoot
$python = Get-Command python3, python, py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $python) {
    Write-ForgeLine "x" "Red" "install: FORGE-PYTHON-NOT-FOUND - Python 3.11+ not found on PATH"
    Write-ForgeLine ">" "Yellow" "remedy: install Python 3.11+ and reopen the terminal"
    exit 2
}
$pythonArgs = @()
if ([IO.Path]::GetFileName($python.Source) -in @("py.exe", "py")) { $pythonArgs = @("-3") }
& $python.Source @pythonArgs (Join-Path $root "scripts\install_portable.py") @args
exit $LASTEXITCODE
