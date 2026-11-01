$ErrorActionPreference = 'Stop'

$backendDirectory = $PSScriptRoot
$pythonPath = Join-Path $backendDirectory '.venv\Scripts\python.exe'
$envFilePath = Join-Path $backendDirectory '.env'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Backend virtual environment is missing. Create backend\.venv and install requirements.txt first.'
}
if (-not (Test-Path -LiteralPath $envFilePath -PathType Leaf)) {
    throw 'backend\.env is missing. Create it from .env.example and configure local settings before starting.'
}

# Ensure Uvicorn loads the backend .env value instead of preserving a stale
# allowlist inherited from the shell that launched this script.
Remove-Item Env:SKILLFAIR_AUTH_ALLOWED_ORIGINS -ErrorAction SilentlyContinue

Push-Location -LiteralPath $backendDirectory
try {
    & $pythonPath -m uvicorn app.main:app --reload --port 8000 --env-file $envFilePath
}
finally {
    Pop-Location
}
