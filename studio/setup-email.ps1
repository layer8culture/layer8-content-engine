<#
Secure local Gmail SMTP setup for Layer8 Studio.

The Google app password is read with masked input. It is never placed on the command
line, printed, or written to shell history. It is stored only in studio\.env, which is
gitignored. Existing .env settings are preserved.

Create the app password yourself first: https://myaccount.google.com/apppasswords
(Google requires 2-Step Verification.)
#>
param(
    [switch]$Test,
    [string]$Python = (Get-Command python -ErrorAction Stop).Source
)

$ErrorActionPreference = "Stop"
$EnvPath = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $EnvPath)) {
    Copy-Item (Join-Path $PSScriptRoot ".env.example") $EnvPath
}

function Read-EnvMap([string]$Path) {
    $map = [ordered]@{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $map[$matches[1]] = $matches[2] }
    }
    return $map
}

function Set-EnvValues([string]$Path, [hashtable]$Values) {
    $lines = [System.Collections.Generic.List[string]]::new()
    $seen = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=') {
            $key = $matches[1]
            if ($Values.ContainsKey($key)) {
                $lines.Add("$key=$($Values[$key])")
                $seen[$key] = $true
                continue
            }
        }
        $lines.Add($line)
    }
    foreach ($key in $Values.Keys) {
        if (-not $seen.ContainsKey($key)) { $lines.Add("$key=$($Values[$key])") }
    }
    $tmp = "$Path.tmp"
    [IO.File]::WriteAllLines($tmp, $lines, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

$current = Read-EnvMap $EnvPath
$account = if ($current["NOTIFY_EMAIL_TO"]) { $current["NOTIFY_EMAIL_TO"] } else {
    Read-Host "Gmail address used to send and receive Layer8 Studio email"
}
if (-not $account) { throw "A Gmail address is required." }

Write-Host "Create a Google app password first at:"
Write-Host "  https://myaccount.google.com/apppasswords"
Write-Host "2-Step Verification must be enabled. Do not enter your normal Google password."
$secure = Read-Host "Paste the 16-character Google app password (input is masked)" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    if (-not $password) { throw "No app password was entered." }
    $password = $password -replace '\s', ''
    Set-EnvValues $EnvPath @{
        NOTIFY_EMAIL_TO = $account
        SMTP_HOST = "smtp.gmail.com"
        SMTP_PORT = "587"
        SMTP_FROM = $account
        SMTP_USER = $account
        SMTP_PASSWORD = $password
        SMTP_STARTTLS = "true"
        SMTP_USE_SSL = "false"
    }
} finally {
    if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    Remove-Variable password -ErrorAction SilentlyContinue
}

Write-Host "Gmail SMTP settings saved to the gitignored studio\.env."
if ($Test) {
    & $Python -u (Join-Path $PSScriptRoot "studio.py") email-test
    if ($LASTEXITCODE -ne 0) { throw "Test email failed (exit $LASTEXITCODE)." }
}
