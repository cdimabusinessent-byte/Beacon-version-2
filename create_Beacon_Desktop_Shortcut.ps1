param(
    [string]$ShortcutName = "Beacon Production",
    [string]$BindAddress = "127.0.0.1",
    [int]$Port = 8000,
    [string]$EnvFile = ".env",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath ("{0}.lnk" -f $ShortcutName)
$TargetPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$ScriptPath = Join-Path $ProjectRoot "start_Beacon_Production.ps1"
$ResolvedEnvFile = if ([System.IO.Path]::IsPathRooted($EnvFile)) { $EnvFile } else { Join-Path $ProjectRoot $EnvFile }

if (-not (Test-Path $ScriptPath)) {
    throw "Production start script not found at $ScriptPath."
}

if (-not (Test-Path $ResolvedEnvFile)) {
    throw "Environment file not found at $ResolvedEnvFile."
}

if ((Test-Path $ShortcutPath) -and (-not $Force.IsPresent)) {
    throw "Shortcut already exists at $ShortcutPath. Use -Force to overwrite it."
}

$Arguments = "-NoExit -ExecutionPolicy Bypass -File `"{0}`" -BindAddress `"{1}`" -Port {2} -EnvFile `"{3}`" -OpenBrowser" -f $ScriptPath, $BindAddress, $Port, $ResolvedEnvFile

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetPath
$Shortcut.Arguments = $Arguments
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.IconLocation = "$TargetPath,0"
$Shortcut.Description = "Launch Beacon in production mode"
$Shortcut.Save()

Write-Host "Created desktop shortcut: $ShortcutPath"
Write-Host "Shortcut target: $TargetPath"
Write-Host "Shortcut arguments: $Arguments"
