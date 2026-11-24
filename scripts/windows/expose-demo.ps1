<#
.SYNOPSIS
  Expose the Call Link Demo frontend (Vite inside WSL, port 5180) to your tailnet only.

.DESCRIPTION
  Run in an ELEVATED Windows PowerShell (Run as administrator). Safe to re-run: every step
  checks what already exists.

  NAT mode (WSL default):
    - netsh portproxy  <tailscale-ip>:5180 -> <wsl-ip>:5180   (replaces any existing entry on that listen address/port)
    - Windows Firewall rule "Call Link Demo 5180 (Tailscale only)": inbound TCP 5180, remote 100.64.0.0/10 only
  Mirrored mode:
    - no portproxy (WSL shares the host's addresses)
    - the same Windows Firewall rule, plus a Hyper-V firewall rule for the WSL VM when its
      default inbound action is Block

  NOTE: in NAT mode the WSL IP changes after a WSL restart or a reboot. Re-run this script then.

  This script never touches Tailscale configuration (no `tailscale serve`, `funnel` or `cert`).
  It only reads `tailscale ip -4` and `tailscale status --json`.
  Undo everything it adds with unexpose-demo.ps1.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu-22.04\home\usr\SimpleAI_Demo\scripts\windows\expose-demo.ps1" -Distro Ubuntu-22.04
#>
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [int]$Port = 5180,
    # WSL distro that runs the demo. Empty = your default distro.
    [string]$Distro = '',
    # auto = ask WSL (`wslinfo --networking-mode`).
    [ValidateSet('auto', 'nat', 'mirrored')]
    [string]$Mode = 'auto'
)

$ErrorActionPreference = 'Stop'

# Keep these names identical in unexpose-demo.ps1.
$RuleName = "Call Link Demo $Port (Tailscale only)"
$RuleGroup = 'Call Link Demo'
$HyperVRuleName = "CallLinkDemo-$Port"
$TailnetRange = '100.64.0.0/10'
$WslVmCreatorId = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'  # Hyper-V firewall id of the WSL VM

function Invoke-Native([scriptblock]$Block) {
    # Native tools write to stderr on harmless conditions; don't let that stop the script.
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Block 2>$null } finally { $ErrorActionPreference = $old }
}

function Invoke-Wsl([string[]]$Command) {
    $wslArgs = @()
    if ($Distro) { $wslArgs += @('-d', $Distro) }
    $wslArgs += '-e'
    $wslArgs += $Command
    $out = Invoke-Native { & wsl.exe @wslArgs }
    if ($LASTEXITCODE -ne 0) { return $null }
    return (($out | Out-String).Trim())
}

function Get-TailscaleExe {
    $cmd = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $path = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (Test-Path $path) { return $path }
    throw 'tailscale.exe not found. Is Tailscale installed and running?'
}

function Test-TailnetIp([string]$Ip) {
    return $Ip -match '^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}$'
}

# --- Tailscale (read-only) --------------------------------------------------------------
$ts = Get-TailscaleExe
$tsIp = ((Invoke-Native { & $ts ip -4 }) | Select-Object -First 1)
if ($tsIp) { $tsIp = $tsIp.Trim() }
if (-not (Test-TailnetIp $tsIp)) { throw "Unexpected Tailscale IPv4 '$tsIp'. Is Tailscale connected?" }
$dnsName = $null
try {
    $status = (Invoke-Native { & $ts status --json }) | Out-String | ConvertFrom-Json
    if ($status.Self.DNSName) { $dnsName = $status.Self.DNSName.TrimEnd('.') }
} catch { }
Write-Host "Tailscale IPv4: $tsIp$(if ($dnsName) { "  ($dnsName)" })"

# --- WSL networking mode ------------------------------------------------------------------
$netMode = $Mode
if ($netMode -eq 'auto') {
    $detected = Invoke-Wsl @('wslinfo', '--networking-mode')
    if ($detected) {
        $netMode = $detected.Trim()
    } else {
        $netMode = 'nat'
        Write-Warning "Couldn't run wslinfo inside WSL; assuming NAT. Pass -Mode mirrored if that's wrong."
    }
}
if ($netMode -notin @('nat', 'mirrored')) { throw "Unsupported WSL networking mode '$netMode' (expected nat or mirrored)." }
Write-Host "WSL networking mode: $netMode"

# --- Windows Firewall: tailnet only (both modes) --------------------------------------------
$existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Firewall rule '$RuleName' already exists."
} else {
    New-NetFirewallRule -DisplayName $RuleName -Group $RuleGroup `
        -Description 'Added by scripts/windows/expose-demo.ps1 (Call Link Demo). Remove with unexpose-demo.ps1.' `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
        -RemoteAddress $TailnetRange -Profile Any | Out-Null
    Write-Host "Created firewall rule '$RuleName' (TCP $Port from $TailnetRange only)."
}

if ($netMode -eq 'nat') {
    # --- NAT: forward the Tailscale address into the WSL VM ----------------------------------
    $wslIp = (Invoke-Wsl @('hostname', '-I')) -split '\s+' |
        Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' } | Select-Object -First 1
    # `hostname -I` order isn't guaranteed (docker bridges have addresses too): prefer eth0.
    $eth0 = Invoke-Wsl @('ip', '-4', '-o', 'addr', 'show', 'dev', 'eth0')
    if ($eth0 -match 'inet (\d{1,3}(?:\.\d{1,3}){3})/') {
        if ($Matches[1] -ne $wslIp) {
            Write-Warning "First address from 'hostname -I' ($wslIp) isn't eth0; using eth0 $($Matches[1])."
            $wslIp = $Matches[1]
        }
    }
    if (-not $wslIp) { throw 'Could not read the WSL IP. Is WSL running?' }
    Write-Host "WSL IP: $wslIp"

    $iphlp = Get-Service iphlpsvc -ErrorAction SilentlyContinue
    if (-not $iphlp -or $iphlp.Status -ne 'Running') {
        Write-Warning "The IP Helper service (iphlpsvc) isn't running, and portproxy needs it: Start-Service iphlpsvc"
    }

    Invoke-Native { netsh interface portproxy delete v4tov4 listenaddress=$tsIp listenport=$Port } | Out-Null
    $added = Invoke-Native { netsh interface portproxy add v4tov4 listenaddress=$tsIp listenport=$Port connectaddress=$wslIp connectport=$Port }
    if ($LASTEXITCODE -ne 0) { throw "netsh portproxy add failed: $added" }
    Write-Host "Port proxy: ${tsIp}:$Port -> ${wslIp}:$Port"
} else {
    # --- Mirrored: WSL listens on the host's addresses; the Hyper-V firewall may block inbound ----
    if (Get-Command New-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
        $vm = Get-NetFirewallHyperVVMSetting -PolicyStore ActiveStore -Name $WslVmCreatorId -ErrorAction SilentlyContinue
        if ($vm -and "$($vm.DefaultInboundAction)" -eq 'Allow') {
            Write-Host 'Hyper-V firewall already allows inbound traffic to WSL; no Hyper-V rule needed.'
        } elseif (Get-NetFirewallHyperVRule -Name $HyperVRuleName -ErrorAction SilentlyContinue) {
            Write-Host "Hyper-V firewall rule '$HyperVRuleName' already exists."
        } else {
            New-NetFirewallHyperVRule -Name $HyperVRuleName -DisplayName $RuleName -Direction Inbound `
                -VMCreatorId $WslVmCreatorId -Protocol TCP -LocalPorts $Port `
                -RemoteAddresses $TailnetRange -Action Allow | Out-Null
            Write-Host "Created Hyper-V firewall rule '$HyperVRuleName' for WSL (TCP $Port from $TailnetRange)."
        }
    } else {
        Write-Host 'No Hyper-V firewall cmdlets on this Windows build; skipping the Hyper-V rule.'
    }
}

# --- Result ---------------------------------------------------------------------------------
$reachable = Test-NetConnection -ComputerName $tsIp -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue
Write-Host ''
Write-Host 'Open on your Mac (tailnet only):' -ForegroundColor Green
Write-Host "  http://${tsIp}:$Port/dev/screenshots"
if ($dnsName) { Write-Host "  http://${dnsName}:$Port/dev/screenshots" }
if (-not $reachable) {
    Write-Warning "Nothing answered on ${tsIp}:$Port yet. Start the servers in WSL: make backend / make frontend."
}
Write-Host 'In NAT mode, re-run this script after a WSL restart or a reboot (the WSL IP changes).'
