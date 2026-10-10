<#
Registers the Layer8 Studio scheduled tasks for the current user (no admin needed).

  LayerStudio-Daily    17:00 every day        python studio.py run-daily   (plan tomorrow, render, prompt pack, gates, preview)
  LayerStudio-Publish  every 30 min, 06:00-23:00  python studio.py publish  (ingest heroes, gates, push due posts to Postiz)

Both use StartWhenAvailable, so a run missed while the laptop was off/asleep fires when it's back.
Remove with:  .\install-schedule.ps1 -Uninstall
Pause posting without touching tasks:  New-Item studio\PAUSE
#>
param(
    [switch]$Uninstall,
    [string]$DailyTime = "17:00",
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
    # conhost --headless: a hidden console that Windows never hands to Windows Terminal (see task.ps1).
    $arg = "--headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Studio\task.ps1`" -Job $cmd -Python `"$Python`""
    if ($SelfUpdate) { $arg += " -SelfUpdate" }
    New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\conhost.exe" -Argument $arg -WorkingDirectory $Studio
}

function New-StudioSettings([int]$Minutes) {
    New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $Minutes) -MultipleInstances IgnoreNew
}
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

$daily = New-ScheduledTaskTrigger -Daily -At $DailyTime
Register-ScheduledTask -TaskName "LayerStudio-Daily" -Action (New-StudioAction "run-daily" -SelfUpdate) -Trigger $daily `
    -Settings (New-StudioSettings 120) -Principal $principal -Description "Layer8 Studio: plan + render tomorrow" -Force | Out-Null

$pub = New-ScheduledTaskTrigger -Daily -At "06:00"
$pub.Repetition = (New-ScheduledTaskTrigger -Once -At "06:00" -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 17)).Repetition
# Don't kill the 23:00 run the moment the repetition window closes.
$pub.Repetition.StopAtDurationEnd = $false
Register-ScheduledTask -TaskName "LayerStudio-Publish" -Action (New-StudioAction "publish") -Trigger $pub `
    -Settings (New-StudioSettings 90) -Principal $principal -Description "Layer8 Studio: publish due posts to Postiz" -Force | Out-Null

Get-ScheduledTask -TaskName $Names | Select-Object TaskName, State | Format-Table

# Desktop shortcut for the manual ChatGPT hero desk (keeps a custom icon if you've set one).
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Layer8 Heroes.lnk"
$sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$icon = Join-Path $env:LOCALAPPDATA "Layer8Studio\heroes.ico"
$keepIcon = $sc.IconLocation
$sc.TargetPath = Join-Path $Studio "heroes.cmd"
$sc.WorkingDirectory = $Studio
$sc.Description = "Layer8 Studio: copy ChatGPT hero prompts and file your downloads"
if (Test-Path $icon) { $sc.IconLocation = "$icon,0" }
elseif ($keepIcon -and $keepIcon -notmatch '^,') { $sc.IconLocation = $keepIcon }
$sc.Save()
Write-Host "Desktop shortcut: $lnk"
Write-Host "Installed. Logs: $Studio\data\logs\  |  Pause: New-Item '$Studio\PAUSE'"
