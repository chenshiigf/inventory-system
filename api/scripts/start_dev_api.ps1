[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repositoryPath = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonPath = Join-Path $repositoryPath 'api/.venv/Scripts/python.exe'
$environmentPath = Join-Path $repositoryPath 'api/.env'

if (-not (Test-Path -LiteralPath $environmentPath -PathType Leaf)) {
    throw 'Missing local api/.env; configure visual administrator settings first.'
}

# This local-only command does not stop other processes, migrate or build anything.
& $pythonPath -m uvicorn app.main:app --app-dir (Join-Path $repositoryPath 'api') `
    --reload --reload-dir (Join-Path $repositoryPath 'api/app') `
    --env-file $environmentPath --host 127.0.0.1 --port 8002
exit $LASTEXITCODE
