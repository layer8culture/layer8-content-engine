param([Parameter(Mandatory=$true)][string]$BackupPath, [switch]$VerifyOnly)
$ErrorActionPreference = 'Stop'
$manifest = Get-Content (Join-Path $BackupPath 'manifest.json') -Raw | ConvertFrom-Json
$site = Join-Path $BackupPath 'restore\site'
foreach ($file in $manifest.files) {
    $path = Join-Path $site $file.path
    if ((Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) {
        throw "Backup checksum mismatch: $($file.path)"
    }
    if ($VerifyOnly) {
        Write-Output "Verified all $($manifest.files.Count) rollback files."
        return
    }
}
$env:SWA_CLI_DEPLOYMENT_TOKEN = az staticwebapp secrets list --name swa-layer8culture --resource-group rg-layer8culture --query properties.apiKey --output tsv
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($env:SWA_CLI_DEPLOYMENT_TOKEN)) {
    throw 'Unable to retrieve deployment credential.'
}
try {
    npx --yes --package '@azure/static-web-apps-cli@2.0.7' swa deploy $site --swa-config-location $site --env production --no-use-keychain
    if ($LASTEXITCODE -ne 0) { throw 'Rollback deployment failed.' }
} finally {
    Remove-Item Env:\SWA_CLI_DEPLOYMENT_TOKEN -ErrorAction SilentlyContinue
}
