$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (Test-Path ".venv\Scripts\python.exe") {
    $python = ".venv\Scripts\python.exe"
} elseif (Test-Path "venv\Scripts\python.exe") {
    $python = "venv\Scripts\python.exe"
} else {
    Write-Host "Virtual environment missing. Run scripts\setup.ps1 first." -ForegroundColor Yellow
    exit 1
}

& $python -m backend
