param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [string]$ConfigFile = "",
    [string]$OwnerId = "",
    [string]$WorkerKey = "",
    [int]$ProfileId = 0,
    [string]$Label = "",
    [string]$TerminalPath = "",
    [string]$ControlApiKey = ""
)

$ErrorActionPreference = "Stop"

function Invoke-ProvisionRequest {
    param(
        [string]$BaseUrl,
        [hashtable]$WorkerItem,
        [string]$ApiKey
    )

    $headers = @{
        "X-Owner-Id" = $WorkerItem.owner_id
        "Content-Type" = "application/json"
    }
    if ($ApiKey) {
        $headers["X-Control-Key"] = $ApiKey
    }

    $payload = @{
        owner_id = $WorkerItem.owner_id
        worker_key = $WorkerItem.worker_key
        profile_id = if ($WorkerItem.profile_id) { [int]$WorkerItem.profile_id } else { $null }
        label = if ($WorkerItem.label) { $WorkerItem.label } else { $null }
        terminal_path = if ($WorkerItem.terminal_path) { $WorkerItem.terminal_path } else { $null }
    }

    $json = $payload | ConvertTo-Json -Depth 4
    Write-Host "Provisioning worker '$($WorkerItem.worker_key)' for owner '$($WorkerItem.owner_id)'..."
    $response = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/mt5/workers/provision" -Headers $headers -Body $json
    return $response
}

$resolvedBaseUrl = $ApiBaseUrl.TrimEnd("/")
$workerItems = @()

if ($ConfigFile) {
    $resolvedConfig = if ([System.IO.Path]::IsPathRooted($ConfigFile)) { $ConfigFile } else { Join-Path (Get-Location) $ConfigFile }
    if (-not (Test-Path $resolvedConfig)) {
        throw "Config file not found at $resolvedConfig."
    }
    $raw = Get-Content -Raw -Path $resolvedConfig | ConvertFrom-Json
    foreach ($item in $raw) {
        $workerItems += @{
            owner_id = [string]$item.owner_id
            worker_key = [string]$item.worker_key
            profile_id = if ($null -ne $item.profile_id) { [int]$item.profile_id } else { 0 }
            label = [string]$item.label
            terminal_path = [string]$item.terminal_path
        }
    }
} else {
    if (-not $OwnerId) {
        throw "OwnerId is required when ConfigFile is not provided."
    }
    if (-not $WorkerKey) {
        throw "WorkerKey is required when ConfigFile is not provided."
    }
    $workerItems += @{
        owner_id = $OwnerId
        worker_key = $WorkerKey
        profile_id = $ProfileId
        label = $Label
        terminal_path = $TerminalPath
    }
}

$results = @()
foreach ($item in $workerItems) {
    $results += Invoke-ProvisionRequest -BaseUrl $resolvedBaseUrl -WorkerItem $item -ApiKey $ControlApiKey
}

Write-Host "Provisioning complete."
$results | ConvertTo-Json -Depth 6
