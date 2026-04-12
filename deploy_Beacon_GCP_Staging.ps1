param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [Parameter(Mandatory = $true)]
    [string]$CloudSqlInstance,

    [Parameter(Mandatory = $true)]
    [string]$TrustedHosts,

    [Parameter(Mandatory = $true)]
    [string]$CorsAllowedOrigins,

    [string]$Region = "us-central1",
    [string]$Repository = "beacon",
    [string]$ServiceName = "beacon-api",
    [string]$ImageName = "beacon",
    [string]$ServiceAccountEmail = "",
    [string]$DatabaseUrlSecretName = "beacon-database-url",
    [string]$ControlApiKeySecretName = "beacon-control-api-key",
    [string]$Mt5ProfileEncryptionKeySecretName = "beacon-mt5-profile-encryption-key",
    [switch]$CreateArtifactRegistryIfMissing
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$CloudBuildFile = Join-Path $ProjectRoot "cloudbuild.yaml"
$ManifestTemplate = Join-Path $ProjectRoot "cloudrun.service.yaml"
$RenderedManifest = Join-Path $ProjectRoot "cloudrun.rendered.staging.yaml"

if (-not (Test-Path $CloudBuildFile)) {
    throw "Cloud Build config not found at $CloudBuildFile."
}
if (-not (Test-Path $ManifestTemplate)) {
    throw "Cloud Run manifest template not found at $ManifestTemplate."
}
if (-not $ServiceAccountEmail) {
    $ServiceAccountEmail = "beacon-api@$ProjectId.iam.gserviceaccount.com"
}

$null = Get-Command gcloud -ErrorAction Stop

Write-Host "Setting active gcloud project: $ProjectId"
gcloud config set project $ProjectId | Out-Null

if ($CreateArtifactRegistryIfMissing.IsPresent) {
    Write-Host "Ensuring Artifact Registry repository exists: $Repository"
    gcloud artifacts repositories describe $Repository --location $Region 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        gcloud artifacts repositories create $Repository --repository-format docker --location $Region --description "Beacon container images"
    }
}

Write-Host "Rendering Cloud Run manifest: $RenderedManifest"
$Manifest = Get-Content $ManifestTemplate -Raw
$Manifest = $Manifest.Replace("PROJECT_ID:REGION:CLOUDSQL_INSTANCE", "$ProjectId`:$Region`:$CloudSqlInstance")
$Manifest = $Manifest.Replace("beacon-api@PROJECT_ID.iam.gserviceaccount.com", $ServiceAccountEmail)
$Manifest = $Manifest.Replace("name: beacon-database-url", "name: $DatabaseUrlSecretName")
$Manifest = $Manifest.Replace("name: beacon-control-api-key", "name: $ControlApiKeySecretName")
$Manifest = $Manifest.Replace("name: beacon-mt5-profile-encryption-key", "name: $Mt5ProfileEncryptionKeySecretName")
$QuotedTrustedHosts = '"' + $TrustedHosts + '"'
$QuotedCorsAllowedOrigins = '"' + $CorsAllowedOrigins + '"'
$Manifest = $Manifest.Replace("value: your-api-domain.run.app,your-api-domain.example.com", "value: $QuotedTrustedHosts")
$Manifest = $Manifest.Replace("value: https://your-frontend.example.com", "value: $QuotedCorsAllowedOrigins")
$Manifest | Set-Content -Path $RenderedManifest -Encoding UTF8

try {
    Write-Host "Submitting Cloud Build for service '$ServiceName' in region '$Region'"
    gcloud builds submit `
        --config $CloudBuildFile `
        --substitutions "_REGION=$Region,_REPOSITORY=$Repository,_SERVICE=$ServiceName,_IMAGE_NAME=$ImageName,_MANIFEST=cloudrun.rendered.staging.yaml"
}
finally {
    if (Test-Path $RenderedManifest) {
        Remove-Item $RenderedManifest -Force
    }
}
