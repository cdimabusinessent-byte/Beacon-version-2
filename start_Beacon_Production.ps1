param(
    [string]$BindAddress = "127.0.0.1",
    [int]$Port = 8000,
    [string]$EnvFile = ".env",
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$env:PYTHONPATH = $ProjectRoot

$ResolvedEnvFile = if ([System.IO.Path]::IsPathRooted($EnvFile)) { $EnvFile } else { Join-Path $ProjectRoot $EnvFile }

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found at $PythonExe. Create .venv and install requirements first."
}

$env:BEACON_ENV_FILE = $ResolvedEnvFile
if (-not (Test-Path $ResolvedEnvFile)) {
    throw ".env not found at $ResolvedEnvFile. Copy .env.example, .env.production.example, or .env.localtest first."
}

$Url = "http://{0}:{1}" -f $BindAddress, $Port
$UvicornArgs = @(
    "-m", "uvicorn",
    "app.main:app",
    "--app-dir", $ProjectRoot,
    "--host", $BindAddress,
    "--port", "$Port"
)

Write-Host "Starting Beacon production launcher from: $ProjectRoot"
Write-Host "Using Python: $PythonExe"
Write-Host "Listening on: $Url"
Write-Host "Env file: $ResolvedEnvFile"
Write-Host "Reload mode: disabled"

Write-Host "Applying database migrations..."
& $PythonExe -m alembic upgrade head

if ($OpenBrowser.IsPresent) {
    Start-Process $Url | Out-Null
}

& $PythonExe @UvicornArgs
