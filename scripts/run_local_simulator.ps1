Param(
    [int]$Port = 8000,
    [string]$GroqApiKey = ""
)

$ErrorActionPreference = "Stop"

# One-shot local setup + run for Symphony simulator UI on Windows PowerShell.
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_local_simulator.ps1
# Optional:
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_local_simulator.ps1 -Port 8000 -GroqApiKey "..."

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

if ($GroqApiKey) {
    $env:GROQ_API_KEY = $GroqApiKey
}
$env:PORT = "$Port"

$VenvDir = Join-Path $RootDir ".venv"
if (!(Test-Path $VenvDir)) {
    py -m venv .venv
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (!(Test-Path $VenvPython)) {
    throw "Virtual environment python not found at $VenvPython"
}

& $VenvPython -m ensurepip --upgrade
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install flask pytest

Write-Host ""
Write-Host "✅ Environment ready."
Write-Host "Run tests (optional):"
Write-Host "  .\.venv\Scripts\python.exe -m pytest -q tests/test_simulator_ui.py"
Write-Host ""
Write-Host "Starting app on http://localhost:$Port/simulator"
Write-Host "Press Ctrl+C to stop."

& $VenvPython -m app.main
