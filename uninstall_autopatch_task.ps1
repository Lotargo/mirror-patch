# PowerShell script to unregister Antigravity Auto-Patcher from Windows Task Scheduler

$taskName = "AntigravityAutoPatcher"
try {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
    Write-Host "[+] Task '$taskName' removed from Task Scheduler." -ForegroundColor Green
} catch {
    Write-Host "[-] Task '$taskName' not found or already removed." -ForegroundColor Yellow
}
