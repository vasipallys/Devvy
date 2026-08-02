$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = if (Test-Path ".venv\Scripts\python.exe") {
    ".venv\Scripts\python.exe"
} elseif (Test-Path "venv\Scripts\python.exe") {
    "venv\Scripts\python.exe"
} else {
    throw "Virtual environment not found. Run scripts\setup.ps1 first."
}

$phoenix = Join-Path (Split-Path $python -Parent) "phoenix.exe"
Write-Host "Phoenix UI and collector: http://127.0.0.1:6006" -ForegroundColor Green
& $phoenix serve
