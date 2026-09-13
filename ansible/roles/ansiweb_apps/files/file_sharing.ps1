# AnsiWEB: turn Windows file and printer sharing on (or off) on this PC.
param([bool]$Enable = $true, [bool]$NetworkDiscovery = $false)

$ErrorActionPreference = 'Stop'
$steps = @()
$changed = $false

function Set-FirewallGroup([string]$Group, [bool]$On) {
    $rules = @(Get-NetFirewallRule -DisplayGroup $Group -ErrorAction SilentlyContinue)
    if (-not $rules) {
        $script:steps += "'$Group' rules are not present on this PC"
        return
    }
    $needing = $rules | Where-Object { ($_.Enabled -eq 'True') -ne $On }
    if ($needing) {
        if ($On) { Enable-NetFirewallRule -DisplayGroup $Group }
        else     { Disable-NetFirewallRule -DisplayGroup $Group }
        $script:steps += "$Group " + $(if ($On) { 'allowed' } else { 'blocked' })
        $script:changed = $true
    } else {
        $script:steps += "$Group already " + $(if ($On) { 'allowed' } else { 'blocked' })
    }
}

Set-FirewallGroup 'File and Printer Sharing' $Enable
if ($NetworkDiscovery) { Set-FirewallGroup 'Network Discovery' $Enable }

# The service that actually serves shares
$srv = Get-Service LanmanServer -ErrorAction SilentlyContinue
if ($Enable -and $srv) {
    if ($srv.StartType -ne 'Automatic') { Set-Service LanmanServer -StartupType Automatic; $changed = $true }
    if ($srv.Status -ne 'Running') { Start-Service LanmanServer; $steps += 'started the Server service'; $changed = $true }
}

$Ansible.Result = @{ state = $(if ($Enable) { 'on' } else { 'off' }); detail = ($steps -join '; ') }
$Ansible.Changed = $changed
