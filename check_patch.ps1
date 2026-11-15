<#
.SYNOPSIS
    Checks the status of Antigravity patches across all installed components.
    If any patch has reverted or is missing, automatically re-patches them,
    re-verifies the status, sends a notification, and exits cleanly.

.DESCRIPTION
    Supported targets:
      - Antigravity IDE (main.js)
      - Antigravity 2.0 (language_server.exe)
      - Antigravity CLI (agy.exe)
      - VS Code Extension (extension.js & agy binary)

.PARAMETER Silent
    Suppresses console output, keeping only notifications and exit codes.

.PARAMETER NoNotify
    Suppresses Windows toast and balloon notifications.

.PARAMETER Force
    Forces complete re-verification. Already patched files are never rewritten.

.EXAMPLE
    .\check_patch.ps1
    .\check_patch.ps1 -Silent
    .\check_patch.ps1 -Force
#>

[CmdletBinding()]
param(
    [switch]$Silent,
    [switch]$NoNotify,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# Set encoding for clean unicode output
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}

# Locate Python executable
$pythonExe = "C:\Users\etotm\AppData\Local\Programs\Python\Python312\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $pythonExe = $pythonCmd.Source
    } else {
        $fallbacks = @(
            "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python314\python.exe",
            "C:\Program Files\Python312\python.exe",
            "C:\Program Files\Python311\python.exe"
        )
        foreach ($fb in $fallbacks) {
            if (Test-Path $fb) {
                $pythonExe = $fb
                break
            }
        }
    }
}

if (-not (Test-Path $pythonExe)) {
    Write-Error "[!] Python executable not found. Please ensure Python is installed and in PATH."
    exit 1
}

$mainScript = Join-Path $scriptDir "source\main.py"
if (-not (Test-Path $mainScript)) {
    Write-Error "[!] source\main.py not found at $mainScript"
    exit 1
}

# Notification helper
function Send-Notification {
    param(
        [string]$Title,
        [string]$Message,
        [ValidateSet("Info", "Warning", "Error")][string]$Type = "Info"
    )

    if ($NoNotify) { return }

    # 1. Native Windows 10/11 Toast via WinRT
    $toastSent = $false
    try {
        $typeToast = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        if ($typeToast) {
            $xmlTemplate = @"
<toast>
    <visual>
        <binding template="ToastGeneric">
            <text>$Title</text>
            <text>$Message</text>
        </binding>
    </visual>
</toast>
"@
            $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
            $xml.LoadXml($xmlTemplate)
            $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Antigravity Patcher").Show($toast)
            $toastSent = $true
        }
    } catch { }

    # 2. System.Windows.Forms.NotifyIcon fallback (System tray balloon)
    if (-not $toastSent) {
        try {
            Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
            Add-Type -AssemblyName System.Drawing -ErrorAction Stop
            $icon = [System.Drawing.SystemIcons]::Information
            $tooltipIcon = [System.Windows.Forms.ToolTipIcon]::Info
            if ($Type -eq "Error") {
                $icon = [System.Drawing.SystemIcons]::Error
                $tooltipIcon = [System.Windows.Forms.ToolTipIcon]::Error
            } elseif ($Type -eq "Warning") {
                $icon = [System.Drawing.SystemIcons]::Warning
                $tooltipIcon = [System.Windows.Forms.ToolTipIcon]::Warning
            }
            $notify = New-Object System.Windows.Forms.NotifyIcon
            $notify.Icon = $icon
            $notify.BalloonTipTitle = $Title
            $notify.BalloonTipText = $Message
            $notify.BalloonTipIcon = $tooltipIcon
            $notify.Visible = $true
            $notify.ShowBalloonTip(4000)
            Start-Sleep -Milliseconds 600
            $notify.Dispose()
        } catch { }
    }
}

function Write-StyledHeader {
    if ($Silent) { return }
    Write-Host ""
    Write-Host "===============================================================" -ForegroundColor Cyan
    Write-Host "       [+] Antigravity Patch Status & Auto-Repair Checker      " -ForegroundColor Cyan
    Write-Host "===============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Show-StatusTable {
    param($components)
    if ($Silent) { return }
    foreach ($c in $components) {
        $shade = switch ($c.status) {
            "patched" { "Green" }
            "not_applicable" { "Cyan" }
            "error" { "Red" }
            "missing" { "DarkGray" }
            default { "Yellow" }
        }
        Write-Host "  [$($c.status.ToUpper())] $($c.name)" -ForegroundColor $shade
        if ($c.path) { Write-Host "      Path: $($c.path)" -ForegroundColor DarkGray }
        Write-Host "      Mode: $($c.mode); file state: $($c.file_status)" -ForegroundColor DarkGray
        if ($c.reason) { Write-Host "      $($c.reason)" -ForegroundColor Yellow }
        if ($c.error) { Write-Host "      $($c.error)" -ForegroundColor Red }
    }
    Write-Host "  Authentication has not been checked." -ForegroundColor Yellow
}

function Invoke-PatchCommand {
    param([string[]]$CommandArgs)
    $stderrPath = [IO.Path]::GetTempFileName()
    try {
        # Код 2 означает неполное покрытие, при этом stdout остаётся валидным JSON.
        $PSNativeCommandUseErrorActionPreference = $false
        $output = & $pythonExe $mainScript @CommandArgs 2> $stderrPath
        $code = $LASTEXITCODE
        if ($code -notin @(0, 2)) {
            $details = Get-Content -Raw -LiteralPath $stderrPath
            throw "Python status command failed (exit $code): $details"
        }
        $data = ($output -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $data) { throw "Python returned an empty status document." }
        return [pscustomobject]@{ Data = $data; ExitCode = $code }
    } finally {
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

Write-StyledHeader
$checked = Invoke-PatchCommand -CommandArgs @("--check", "--json")
$status = $checked.Data
Show-StatusTable -components $status.components

if ($status.actionable_count -gt 0 -or $Force) {
    $commandArgs = @("--check-and-patch", "--json")
    if ($Force) { $commandArgs += "--force" }
    $repaired = Invoke-PatchCommand -CommandArgs $commandArgs
    $status = $repaired.Data.final_status
    Show-StatusTable -components $status.components
    $success = $repaired.Data.success -eq $true -and $repaired.ExitCode -eq 0
} else {
    $success = $status.all_patched -eq $true -and $checked.ExitCode -eq 0
}

if ($success) {
    if (-not $Silent) {
        Write-Host "[+] Required local file patches are verified. Authentication is not checked." -ForegroundColor Green
    }
    Send-Notification -Title "Antigravity local patch status" -Message "Local file patches verified. Authentication not checked." -Type "Info"
    exit 0
}

$issues = $status.issue_components -join ", "
if (-not $issues) { $issues = "No installations or incomplete verification" }
if (-not $Silent) {
    Write-Host "[!] Local patch coverage is incomplete: $issues" -ForegroundColor Yellow
}
Send-Notification -Title "Antigravity needs attention" -Message $issues -Type "Warning"
exit 2
