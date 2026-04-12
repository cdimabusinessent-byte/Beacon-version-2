param(
    [string]$BindAddress = "0.0.0.0",
    [int]$Port = 8001,
    [string]$EnvFile = ".env",
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$env:PYTHONPATH = $ProjectRoot

$ResolvedEnvFile = if ([System.IO.Path]::IsPathRooted($EnvFile)) { $EnvFile } else { Join-Path $ProjectRoot $EnvFile }
if (-not (Test-Path $ResolvedEnvFile)) {
    throw "Environment file not found at $ResolvedEnvFile."
}

$env:BEACON_ENV_FILE = $ResolvedEnvFile

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found at $PythonExe. Create venv and install requirements first."
}

$UvicornArgs = @(
    "-m", "uvicorn",
    "app.main:app",
    "--app-dir", $ProjectRoot,
    "--host", $BindAddress,
    "--port", "$Port"
)

if ($Reload.IsPresent) {
    $UvicornArgs += "--reload"
}

Write-Host "Starting Beacon from: $ProjectRoot"
Write-Host "Using Python: $PythonExe"
Write-Host "URL: http://$BindAddress`:$Port"
Write-Host "Env file: $ResolvedEnvFile"

Write-Host "Applying database migrations..."
& $PythonExe -m alembic upgrade head

& $PythonExe @UvicornArgs