<#
.SYNOPSIS
    Convenience alias/forwarder for check_patch.ps1
#>

[CmdletBinding()]
param(
    [switch]$Silent,
    [switch]$NoNotify,
    [switch]$Force
)

$targetScript = Join-Path $PSScriptRoot "check_patch.ps1"
& $targetScript @PSBoundParameters
