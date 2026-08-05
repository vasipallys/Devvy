$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$created = $false
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($version in @("3.13", "3.12", "3.11")) {
        py "-$version" -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) {
            py "-$version" -m venv .venv
            $created = $true
            break
        }
    }
}
if (-not $created) {
    python -c "import sys; assert (3, 11) <= sys.version_info[:2] < (3, 14), 'Python 3.11-3.13 is required'"
    python -m venv .venv
}
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -e ".[dev,image]"
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }

Push-Location frontend
npm install
Pop-Location
Write-Host "Setup complete. Add HF_TOKEN to .env, then run scripts\start-backend.ps1 and, in a second terminal, 'cd frontend; npm run dev'." -ForegroundColor Green
