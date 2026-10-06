<#
Registers the Layer8 Studio scheduled tasks for the current user (no admin needed).

  LayerStudio-Daily    19:00 every day        python studio.py run-daily   (plan tomorrow, render, prompt pack, gates, preview)
  LayerStudio-Publish  every 30 min, 06:00-23:00  python studio.py publish  (ingest heroes, gates, push due posts to Postiz)

Both use StartWhenAvailable, so a run missed while the laptop was off/asleep fires when it's back.
Remove with:  .\install-schedule.ps1 -Uninstall
Pause posting without touching tasks:  New-Item studio\PAUSE
#>
param(
    [switch]$Uninstall,
    [string]$DailyTime = "19:00",
    [string]$Python = (Get-Command python -ErrorAction Stop).Source
)

$ErrorActionPreference = "Stop"
$Studio = $PSScriptRoot
$Names = @("LayerStudio-Daily", "LayerStudio-Publish")

if ($Uninstall) {
    foreach ($n in $Names) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
    Write-Host "Removed Layer8 Studio tasks."
    return
}

New-Item -ItemType Directory -Force (Join-Path $Studio "data\logs") | Out-Null

function New-StudioAction([string]$cmd, [switch]$SelfUpdate) {
    $log = Join-Path $Studio "data\logs\task-$cmd.log"
    # Self-update: fast-forward the runtime clone before planning (failures are logged, never fatal).
    $pre = if ($SelfUpdate) { "git -C '$Studio' pull --ff-only -q *>> '$log'; " } else { "" }
    $arg = "-NoProfile -WindowStyle Hidden -Command `"$pre& '$Python' '$Studio\studio.py' $cmd *>> '$log'`""
    New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $Studio
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

$daily = New-ScheduledTaskTrigger -Daily -At $DailyTime
Register-ScheduledTask -TaskName "LayerStudio-Daily" -Action (New-StudioAction "run-daily" -SelfUpdate) -Trigger $daily `
    -Settings $settings -Principal $principal -Description "Layer8 Studio: plan + render tomorrow" -Force | Out-Null

$pub = New-ScheduledTaskTrigger -Daily -At "06:00"
$pub.Repetition = (New-ScheduledTaskTrigger -Once -At "06:00" -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 17)).Repetition
Register-ScheduledTask -TaskName "LayerStudio-Publish" -Action (New-StudioAction "publish") -Trigger $pub `
    -Settings $settings -Principal $principal -Description "Layer8 Studio: publish due posts to Postiz" -Force | Out-Null

Get-ScheduledTask -TaskName $Names | Select-Object TaskName, State | Format-Table

# Desktop shortcut for the manual ChatGPT hero desk.
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Layer8 Heroes.lnk"
$sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$sc.TargetPath = Join-Path $Studio "heroes.cmd"
$sc.WorkingDirectory = $Studio
$sc.Description = "Layer8 Studio: copy ChatGPT hero prompts and file your downloads"
$sc.Save()
Write-Host "Desktop shortcut: $lnk"
Write-Host "Installed. Logs: $Studio\data\logs\  |  Pause: New-Item '$Studio\PAUSE'"
