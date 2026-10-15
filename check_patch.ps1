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
    Forces re-patching even if all components report as already patched.

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

    Write-Host "  Current Component Status:" -ForegroundColor White
    Write-Host "  -------------------------------------------------------------" -ForegroundColor DarkGray
    foreach ($c in $components) {
        $name = $c.name.PadRight(28)
        if (-not $c.installed) {
            Write-Host "  [-] $name" -NoNewline -ForegroundColor DarkGray
            Write-Host " [NOT INSTALLED]" -ForegroundColor DarkGray
        } elseif ($c.patched -eq $true) {
            Write-Host "  [+] $name" -NoNewline -ForegroundColor Green
            Write-Host " [PATCHED]" -ForegroundColor Green
            Write-Host "      Path: $($c.path)" -ForegroundColor DarkGray
        } else {
            Write-Host "  [!] $name" -NoNewline -ForegroundColor Red
            Write-Host " [NOT PATCHED / REVERTED]" -ForegroundColor Red
            Write-Host "      Path: $($c.path)" -ForegroundColor Yellow
        }
    }
    Write-Host "  -------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host ""
}

# --- Execution Flow ---

Write-StyledHeader

# Step 1: Initial Status Check
if (-not $Silent) {
    Write-Host "[*] Checking patch state across installed components..." -ForegroundColor Cyan
}

$rawCheck = & $pythonExe $mainScript --check --json 2>&1
$rawCheckText = ($rawCheck | Out-String)
$firstBrace = $rawCheckText.IndexOf('{')
$lastBrace = $rawCheckText.LastIndexOf('}')

if ($firstBrace -lt 0 -or $lastBrace -le $firstBrace) {
    Write-Error "[!] Failed to find JSON output from Python patch check.`nRaw output:`n$rawCheckText"
    Send-Notification -Title "Antigravity Patch Error" -Message "Failed to check patch status." -Type "Error"
    exit 1
}

$jsonString = $rawCheckText.Substring($firstBrace, $lastBrace - $firstBrace + 1)

try {
    $status = $jsonString | ConvertFrom-Json
} catch {
    Write-Error "[!] Failed to parse patch check status JSON: $_`nRaw text:`n$jsonString"
    Send-Notification -Title "Antigravity Patch Error" -Message "Failed to parse patch status." -Type "Error"
    exit 1
}

Show-StatusTable -components $status.components

$needsRepatch = ($status.all_patched -eq $false -and $status.unpatched_count -gt 0) -or $Force

if ($needsRepatch) {
    $unpatchedList = ($status.unpatched_components -join ", ")
    if ($Force -and -not $unpatchedList) {
        $unpatchedList = "Forced re-patch requested"
    }

    if (-not $Silent) {
        Write-Host "[!] Found unpatched / reverted targets: $unpatchedList" -ForegroundColor Yellow
        Write-Host "[*] Automatically applying patch..." -ForegroundColor Yellow
    }

    # Execute check-and-patch
    $rawRepatch = & $pythonExe $mainScript --check-and-patch --json 2>&1
    $rawRepatchText = ($rawRepatch | Out-String)
    $firstBraceR = $rawRepatchText.IndexOf('{')
    $lastBraceR = $rawRepatchText.LastIndexOf('}')

    if ($firstBraceR -lt 0 -or $lastBraceR -le $firstBraceR) {
        Write-Error "[!] Error during auto-patch execution (no JSON returned):`n$rawRepatchText"
        Send-Notification -Title "Antigravity Patch Error" -Message "Error during auto-patching." -Type "Error"
        exit 1
    }

    $repatchJson = $rawRepatchText.Substring($firstBraceR, $lastBraceR - $firstBraceR + 1)

    try {
        $result = $repatchJson | ConvertFrom-Json
    } catch {
        Write-Error "[!] Error parsing auto-patch result JSON: $_`nRaw text:`n$repatchJson"
        Send-Notification -Title "Antigravity Patch Error" -Message "Error during auto-patching." -Type "Error"
        exit 1
    }

    # Step 2: Verification check after re-patch
    if (-not $Silent) {
        Write-Host "[*] Re-verifying patch status after auto-repair..." -ForegroundColor Cyan
    }

    Show-StatusTable -components $result.final_status.components

    if ($result.success -eq $true) {
        if (-not $Silent) {
            Write-Host "[+] All Antigravity components successfully verified and patched!" -ForegroundColor Green
            Write-Host ""
        }
        Send-Notification -Title "Antigravity Patch" -Message "All components verified and patched successfully!" -Type "Info"
        exit 0
    } else {
        $failedTargets = ($result.final_status.unpatched_components -join ", ")
        if (-not $Silent) {
            Write-Host "[-] Auto-patch failed for targets: $failedTargets" -ForegroundColor Red
            Write-Host "    Please ensure processes are closed or run PowerShell as Administrator." -ForegroundColor Yellow
            Write-Host ""
        }
        Send-Notification -Title "Antigravity Patch Failed" -Message "Failed to patch: $failedTargets" -Type "Error"
        exit 2
    }
} else {
    # All components already patched
    if (-not $Silent) {
        Write-Host "[+] All installed Antigravity components are already patched and active!" -ForegroundColor Green
        Write-Host ""
    }
    Send-Notification -Title "Antigravity Patch" -Message "All Antigravity components are verified and active!" -Type "Info"
    exit 0
}
