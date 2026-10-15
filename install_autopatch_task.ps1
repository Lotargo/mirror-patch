# PowerShell script to register Antigravity Auto-Patcher in Windows Task Scheduler

$ErrorActionPreference = "Stop"

$taskName = "AntigravityAutoPatcher"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = "C:\Users\etotm\AppData\Local\Programs\Python\Python312\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $pythonw = Join-Path (Split-Path $pythonCmd.Source) "pythonw.exe"
    }
}

$mainScript = Join-Path $scriptDir "source\main.py"
$arguments = "`"$mainScript`" --auto"

Write-Host "[*] Configuring Scheduled Task: $taskName" -ForegroundColor Cyan
Write-Host "    Executable: $pythonw"
Write-Host "    Arguments:  $arguments"
Write-Host "    WorkDir:    $scriptDir"

$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $scriptDir

# Trigger 1: At Logon of current user
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Trigger 2: Repeating daily every 4 hours
$triggerDaily = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 4)

# Settings: Don't stop if running on batteries, wake to run false, allow start on demand
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

# Unregister old task if exists
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

# Register task for current user
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($triggerLogon, $triggerDaily) -Settings $settings -Description "Auto-patches Antigravity components if regional lock was restored by background updates."

Write-Host "[+] Task '$taskName' successfully registered!" -ForegroundColor Green
Write-Host "    Trigger: At user log on + every 4 hours"
