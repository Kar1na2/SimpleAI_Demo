<#
.SYNOPSIS
  Remove exactly what expose-demo.ps1 added for the Call Link Demo. Nothing else.

.DESCRIPTION
  Run in an ELEVATED Windows PowerShell. Safe to re-run.
    - netsh portproxy entries on port 5180 whose listen address is a tailnet IP (100.64.0.0/10)
    - Windows Firewall rule "Call Link Demo 5180 (Tailscale only)" in group "Call Link Demo"
    - Hyper-V firewall rule "CallLinkDemo-5180" (mirrored mode), if present
  Works even when Tailscale is stopped or the WSL IP has changed.
  Never touches Tailscale configuration.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu-22.04\home\usr\SimpleAI_Demo\scripts\windows\unexpose-demo.ps1"
#>
#Requires -RunAsAdministrator
[CmdletBinding()]
param([int]$Port = 5180)

$ErrorActionPreference = 'Stop'

# Keep these names identical in expose-demo.ps1.
$RuleName = "Call Link Demo $Port (Tailscale only)"
$RuleGroup = 'Call Link Demo'
$HyperVRuleName = "CallLinkDemo-$Port"

function Invoke-Native([scriptblock]$Block) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Block 2>$null } finally { $ErrorActionPreference = $old }
}

function Test-TailnetIp([string]$Ip) {
    return $Ip -match '^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}$'
}

# --- Port proxy -------------------------------------------------------------------------------
$removed = 0
foreach ($line in (Invoke-Native { netsh interface portproxy show v4tov4 })) {
    if ($line -match '^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+(\d+)\s+\S+\s+\d+\s*$') {
        $listenAddress = $Matches[1]
        $listenPort = [int]$Matches[2]
        if ($listenPort -eq $Port -and (Test-TailnetIp $listenAddress)) {
            Invoke-Native { netsh interface portproxy delete v4tov4 listenaddress=$listenAddress listenport=$listenPort } | Out-Null
            Write-Host "Removed port proxy ${listenAddress}:$listenPort"
            $removed++
        }
    }
}
if ($removed -eq 0) { Write-Host "No Call Link Demo port proxy on port $Port." }

# --- Windows Firewall -------------------------------------------------------------------------
$rules = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Where-Object { $_.Group -eq $RuleGroup }
if ($rules) {
    $rules | Remove-NetFirewallRule
    Write-Host "Removed firewall rule '$RuleName'."
} else {
    Write-Host "No firewall rule '$RuleName'."
}

# --- Hyper-V firewall (mirrored mode) ---------------------------------------------------------
if (Get-Command Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
    $hv = Get-NetFirewallHyperVRule -Name $HyperVRuleName -ErrorAction SilentlyContinue
    if ($hv) {
        $hv | Remove-NetFirewallHyperVRule
        Write-Host "Removed Hyper-V firewall rule '$HyperVRuleName'."
    } else {
        Write-Host "No Hyper-V firewall rule '$HyperVRuleName'."
    }
}

Write-Host 'Done. The demo is no longer reachable from the tailnet.' -ForegroundColor Green
