<#
.SYNOPSIS
  Disturbs the ADS connection between this Windows PLC and the IOC host, on a timed schedule, and logs every event.

.DESCRIPTION
  Mode Firewall (default): blocks TCP traffic on the ADS port to and from the IOC host for a set time, then lifts the block.
    KNOWN LIMITATION (tested 2026-11-17 on Windows 10): a Windows Firewall block rule does NOT affect an already
    established TCP connection, so an 8 s block left a running IOC untouched. It only blocks new connections.
    Use profiling/ads_stall_proxy.py (or tc/nft on the IOC host) to stall a live connection.
  Mode Nic: disables and re-enables a network adapter. This breaks the TCP session like a cable pull. Do not use it
    on the adapter that carries your Remote Desktop session.

  Run in an elevated PowerShell (Run as administrator). The rules touch only traffic with the IOC host address.
  Rules are removed on exit, also on Ctrl-C.

.PARAMETER RemoteIp   IOC host address (default 192.168.0.6).
.PARAMETER Port       ADS TCP port on the PLC (default 48898).
.PARAMETER StallMs    Stall durations in milliseconds, run in order (default 500,1000,2000,3000,5000).
.PARAMETER Repeats    Times each duration is repeated (default 3).
.PARAMETER GapSec     Seconds to wait before each stall (default 90). The IOC needs about 60 s after a start
                      before its state poll runs, so keep this at 90 or more when the IOC restarts after each stall.
.PARAMETER Mode       Firewall or Nic.
.PARAMETER Adapter    Adapter name for Nic mode (see Get-NetAdapter).
.PARAMETER Log        CSV file for the event log.
.PARAMETER DryRun     Print the schedule and exit without touching the network.

.EXAMPLE
  .\disturb-network.ps1 -StallMs 1000 -Repeats 1
.EXAMPLE
  .\disturb-network.ps1 -StallMs 500,1000,2000,3000,5000 -Repeats 3 -GapSec 120
#>
[CmdletBinding()]
param(
    [string]$RemoteIp = '192.168.0.6',
    [int]$Port = 48898,
    [int[]]$StallMs = @(500, 1000, 2000, 3000, 5000),
    [int]$Repeats = 3,
    [int]$GapSec = 90,
    [ValidateSet('Firewall', 'Nic')][string]$Mode = 'Firewall',
    [string]$Adapter,
    [string]$Log = (Join-Path $PSScriptRoot ("disturb-{0}.csv" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))),
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ruleIn = 'ADSDISTURB-in'
$ruleOut = 'ADSDISTURB-out'

function Stamp { (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff') }

# netsh toggles a rule in about 0.1 to 0.3 s. Enable-NetFirewallRule takes about 2 s per call, which sets a 2 s floor.
function Set-Block([bool]$On) {
    $v = if ($On) { 'yes' } else { 'no' }
    netsh advfirewall firewall set rule name=$ruleIn new enable=$v | Out-Null
    netsh advfirewall firewall set rule name=$ruleOut new enable=$v | Out-Null
}

function Write-Event([string]$Event, [string]$Detail) {
    $line = '{0},{1},{2}' -f (Stamp), $Event, $Detail
    Add-Content -Path $Log -Value $line
    Write-Host $line
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in an elevated PowerShell (Run as administrator).'
}
if ($Mode -eq 'Nic' -and -not $Adapter) {
    Get-NetAdapter | Format-Table Name, InterfaceDescription, Status
    throw 'Nic mode needs -Adapter <name>.'
}

$schedule = foreach ($ms in $StallMs) { 1..$Repeats | ForEach-Object { $ms } }
Write-Host ("Mode={0} RemoteIp={1} Port={2} stalls={3} gap={4}s log={5}" -f $Mode, $RemoteIp, $Port, ($schedule -join ','), $GapSec, $Log)
Write-Host ("Estimated run time: {0} min" -f [math]::Round(($schedule.Count * $GapSec + ($schedule | Measure-Object -Sum).Sum / 1000) / 60, 1))
if ($DryRun) { return }

'timestamp,event,detail' | Set-Content -Path $Log
Write-Event 'SCRIPT_START' ("mode=$Mode remote=$RemoteIp port=$Port clock_utc={0}" -f (Get-Date).ToUniversalTime().ToString('HH:mm:ss.fff'))

try {
    if ($Mode -eq 'Firewall') {
        # Rules are created disabled and toggled per stall, so each switch takes milliseconds.
        Remove-NetFirewallRule -DisplayName $ruleIn, $ruleOut -ErrorAction SilentlyContinue
        New-NetFirewallRule -DisplayName $ruleIn -Direction Inbound -Action Block -Protocol TCP `
            -LocalPort $Port -RemoteAddress $RemoteIp -Enabled False | Out-Null
        New-NetFirewallRule -DisplayName $ruleOut -Direction Outbound -Action Block -Protocol TCP `
            -LocalPort $Port -RemoteAddress $RemoteIp -Enabled False | Out-Null
    }

    $n = 0
    foreach ($ms in $schedule) {
        $n++
        Write-Event 'WAIT' ("stall=$n/$($schedule.Count) planned_ms=$ms gap_s=$GapSec")
        Start-Sleep -Seconds $GapSec

        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        if ($Mode -eq 'Firewall') {
            Set-Block $true
        } else {
            Disable-NetAdapter -Name $Adapter -Confirm:$false
        }
        Write-Event 'STALL_START' ("stall=$n planned_ms=$ms")

        $left = $ms - [int]$sw.ElapsedMilliseconds
        if ($left -gt 0) { Start-Sleep -Milliseconds $left }

        Write-Event 'STALL_LIFT' ("stall=$n held_ms={0}" -f [int]$sw.ElapsedMilliseconds)
        if ($Mode -eq 'Firewall') {
            Set-Block $false
        } else {
            Enable-NetAdapter -Name $Adapter -Confirm:$false
        }
        Write-Event 'STALL_END' ("stall=$n total_ms={0}" -f [int]$sw.ElapsedMilliseconds)
    }
}
finally {
    if ($Mode -eq 'Firewall') {
        Remove-NetFirewallRule -DisplayName $ruleIn, $ruleOut -ErrorAction SilentlyContinue
    } elseif ($Adapter) {
        Enable-NetAdapter -Name $Adapter -Confirm:$false -ErrorAction SilentlyContinue
    }
    Write-Event 'SCRIPT_END' 'rules removed, adapter enabled'
}
