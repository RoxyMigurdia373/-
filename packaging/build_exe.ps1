<#
.SYNOPSIS
    Build the shareable single-file Windows executable.

.DESCRIPTION
    Thin wrapper around packaging/build_exe.py. Writes
    dist\<app name>_v<version>.exe (PyInstaller onefile, windowed).
    Requires: .\.venv\Scripts\python.exe -m pip install -e ".[build]"

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_exe.ps1 -SelfTest
#>
[CmdletBinding()]
param(
    [switch]$SelfTest,
    [switch]$SkipClean
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Missing virtual environment interpreter: $python"
}

$arguments = @("packaging\build_exe.py")
if ($SelfTest) { $arguments += "--selftest" }
if ($SkipClean) { $arguments += "--no-clean" }

Push-Location $root
try {
    & $python @arguments
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($code -ne 0) { throw "Build failed (exit code $code)" }
Write-Host "Done."
exit 0
