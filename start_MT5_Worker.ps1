param(
    [string]$EnvFile = ".env.worker",
    [string]$OwnerId = "",
    [string]$WorkerKey = "",
    [string]$WorkerLabel = "",
    [switch]$RunMigrations
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$env:PYTHONPATH = $ProjectRoot

$ResolvedEnvFile = if ([System.IO.Path]::IsPathRooted($EnvFile)) { $EnvFile } else { Join-Path $ProjectRoot $EnvFile }
if (-not (Test-Path $ResolvedEnvFile)) {
    throw "Environment file not found at $ResolvedEnvFile."
}

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found at $PythonExe. Create .venv and install requirements first."
}

$env:BEACON_ENV_FILE = $ResolvedEnvFile
if ($OwnerId) {
    $env:MT5_RUNTIME_OWNER_ID = $OwnerId
}
if ($WorkerKey) {
    $env:MT5_WORKER_KEY = $WorkerKey
}
if ($WorkerLabel) {
    $env:MT5_WORKER_LABEL = $WorkerLabel
}

Write-Host "Starting MT5 worker from: $ProjectRoot"
Write-Host "Using Python: $PythonExe"
Write-Host "Env file: $ResolvedEnvFile"
Write-Host "Runtime owner: $($env:MT5_RUNTIME_OWNER_ID)"
Write-Host "Worker key: $($env:MT5_WORKER_KEY)"
Write-Host "Worker label: $($env:MT5_WORKER_LABEL)"

if ($RunMigrations.IsPresent) {
    Write-Host "Applying database migrations..."
    & $PythonExe -m alembic upgrade head
}

& $PythonExe -m app.mt5_worker
