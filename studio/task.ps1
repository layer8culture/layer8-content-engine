<#
Scheduled-task entry point. install-schedule.ps1 launches it as
  conhost.exe --headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File task.ps1 -Job <cmd>

conhost --headless owns the console, so Windows never hands it to Windows Terminal (the
"default terminal" setting). On 2026-10-07 one hung Windows Terminal instance blocked every
later task launch at process start: nothing logged and LastTaskResult 0x41306 (terminated).
Every run writes a start line and an end line with the real exit code to data\logs\task-<job>.log.
#>
param(
    [Parameter(Mandatory)][ValidateSet("publish", "run-daily")][string]$Job,
    [switch]$SelfUpdate,
    [string]$Python = "python"
)

$Studio = $PSScriptRoot
$log = Join-Path $Studio "data\logs\task-$Job.log"
$health = Join-Path $Studio "data\health\task-$Job.json"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
New-Item -ItemType Directory -Force (Split-Path $health) | Out-Null
function Write-Log([string]$m) { [IO.File]::AppendAllText($log, "$m`r`n", (New-Object Text.UTF8Encoding($false))) }
function Write-Health([string]$status, [int]$code) {
    $tmp = "$health.$PID.tmp"
    $body = [ordered]@{
        job = $Job
        status = $status
        pid = $PID
        exit_code = $code
        started_at = $t0.ToString("o")
        updated_at = (Get-Date).ToString("o")
    } | ConvertTo-Json
    [IO.File]::WriteAllText($tmp, $body, (New-Object Text.UTF8Encoding($false)))
    Move-Item -Force $tmp $health
}

$t0 = Get-Date
Write-Log "=== $($t0.ToString('s')) start $Job (pid $PID)"
Write-Health "running" -1
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:GIT_TERMINAL_PROMPT = "0"
Set-Location $Studio

if ($SelfUpdate) {
    # Fast-forward the runtime clone; failures are logged, never fatal.
    $out = & git -C $Studio pull --ff-only -q 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Log "git pull failed ($LASTEXITCODE): $out" }
}

# cmd redirection appends Python's UTF-8 bytes as-is (PowerShell 5.1's *>> re-encodes them).
& cmd.exe /d /c "`"$Python`" -u `"$Studio\studio.py`" $Job >> `"$log`" 2>&1"
$rc = $LASTEXITCODE
Write-Log "=== $((Get-Date).ToString('s')) end $Job exit $rc ($([int]((Get-Date) - $t0).TotalSeconds)s)"
Write-Health $(if ($rc -eq 0) { "complete" } else { "failed" }) $rc
exit $rc
