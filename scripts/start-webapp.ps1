[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [string]$DataRoot
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Url = "http://127.0.0.1:$Port"
$script:ProbeFailure = ''
$RequestedDataRoot = $null
if ($DataRoot) {
    $resolved = Get-Item -LiteralPath $DataRoot
    if (-not $resolved.PSIsContainer) {
        throw '-DataRoot must be an existing checkout directory.'
    }
    $RequestedDataRoot = $resolved.FullName
}

function Get-AppSession {
    try {
        $session = Invoke-RestMethod -Uri "$Url/api/session" -TimeoutSec 2
        if ($session.csrf -and $session.diagnostics -is [array]) {
            return $session
        }
        $script:ProbeFailure = 'The listener does not provide the guided app session contract.'
    }
    catch {
        if ($_.Exception -isnot [System.Net.WebException] -and
            $_.Exception.GetType().FullName -notin @(
                'Microsoft.PowerShell.Commands.HttpResponseException',
                'System.Net.Http.HttpRequestException',
                'System.Threading.Tasks.TaskCanceledException',
                'System.OperationCanceledException'
            )) {
            throw
        }
        $script:ProbeFailure = $_.Exception.Message
    }
    return $null
}

function Show-App([object]$Session) {
    Write-Host "Content Engine is ready at $Url"
    foreach ($diagnostic in $Session.diagnostics) {
        if (-not $diagnostic.ok) {
            Write-Warning "$($diagnostic.name): $($diagnostic.detail)"
        }
    }
    Write-Host 'Missing dependencies are not installed automatically. See Environment diagnostics in Batches.'
    if (-not $NoBrowser) {
        Start-Process $Url
    }
}

function Confirm-DataRoot([object]$Session, [bool]$ExistingListener) {
    if (-not $RequestedDataRoot) { return }
    if (-not $Session.data_root) {
        if ($ExistingListener) {
            throw "A guided app is already running at $Url, but it does not identify its data root. Refusing to silently ignore -DataRoot. Use a different -Port or restart that known server."
        }
        return
    }
    $actual = [System.IO.Path]::GetFullPath($Session.data_root).TrimEnd('\')
    $requested = [System.IO.Path]::GetFullPath($RequestedDataRoot).TrimEnd('\')
    if (-not [string]::Equals($actual, $requested, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The app at $Url uses a different data root: $actual. Requested: $requested. Use a different -Port or restart that known server. No process was stopped."
    }
}

# A second launch reuses a healthy app without starting another server.
$existing = Get-AppSession
if ($null -ne $existing) {
    Confirm-DataRoot $existing $true
    Show-App $existing
    return
}

$client = [System.Net.Sockets.TcpClient]::new()
try {
    $connect = $client.ConnectAsync('127.0.0.1', $Port)
    try {
        $null = $connect.Wait(1500)
    }
    catch [System.AggregateException] {
        if ($_.Exception.InnerException -isnot [System.Net.Sockets.SocketException]) { throw }
    }
    if ($client.Connected) {
        throw "Port $Port is already occupied, but the guided app health probe failed: $script:ProbeFailure Use -Port with a different port, or stop the known old server yourself. No process was stopped."
    }
}
finally {
    $client.Dispose()
}

$python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$prefix = @()
if (-not $python) {
    $python = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $prefix = @('-3')
}
if (-not $python) {
    throw 'Python 3.11 or newer is required. Install it explicitly, then run this launcher again.'
}
& $python.Source @prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
if ($LASTEXITCODE -ne 0) {
    throw 'The selected Python could not run or is older than 3.11. Configure Python explicitly; no packages were installed.'
}

$server = Join-Path $PSScriptRoot 'adhoc_server.py'
$logDir = Join-Path $RepoRoot '.local'
$null = New-Item -ItemType Directory -Path $logDir -Force
$logName = 'webapp-launch-{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID
$stdout = Join-Path $logDir "$logName.log"
$stderr = Join-Path $logDir "$logName.error.log"
$arguments = $prefix + @(('"' + $server + '"'), '--no-browser', '--port', "$Port")
if ($RequestedDataRoot) {
    # Appending a directory separator plus dot avoids a trailing backslash
    # escaping the closing quote in Windows command-line argument parsing.
    $arguments += @('--data-root', ('"' + (Join-Path $RequestedDataRoot '.') + '"'))
}
$process = Start-Process -FilePath $python.Source -ArgumentList $arguments -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru

for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    $process.Refresh()
    if ($process.HasExited) {
        throw "The server exited with code $($process.ExitCode). Read $stderr for dependency or startup errors. No dependencies were installed."
    }
    $session = Get-AppSession
    if ($null -ne $session) {
        Confirm-DataRoot $session $false
        Show-App $session
        return
    }
}
throw "The server (PID $($process.Id)) has not passed its health probe. Read $stderr and $stdout. Last probe: $script:ProbeFailure No unrelated process was stopped."
