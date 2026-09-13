# AnsiWEB: turn file and printer sharing on (or off) for a PC.
param([bool]$Enabled = $true, [string]$Profiles = 'Domain,Private')

$ErrorActionPreference = 'Stop'
$steps = @()
$changed = $false
$wanted = [bool]$Enabled

# The Server service is what actually offers shares
$svc = Get-Service LanmanServer
if ($wanted) {
    if ($svc.StartType -ne 'Automatic') {
        Set-Service LanmanServer -StartupType Automatic
        $steps += 'Server service set to start automatically'
        $changed = $true
    }
    if ($svc.Status -ne 'Running') {
        Start-Service LanmanServer
        $steps += 'Server service started'
        $changed = $true
    }
}

# The firewall group Windows itself uses for file and printer sharing
$profileList = @($Profiles -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$rules = Get-NetFirewallRule -DisplayGroup 'File and Printer Sharing' -ErrorAction SilentlyContinue
if (-not $rules) { throw 'The File and Printer Sharing firewall rules are not present on this PC.' }

foreach ($rule in $rules) {
    $inProfile = $profileList | Where-Object { $rule.Profile -match $_ -or $rule.Profile -eq 'Any' }
    if (-not $inProfile) { continue }
    $isOn = $rule.Enabled -eq 'True' -or $rule.Enabled -eq $true
    if ($isOn -ne $wanted) {
        Set-NetFirewallRule -Name $rule.Name -Enabled $(if ($wanted) { 'True' } else { 'False' })
        $changed = $true
    }
}
$steps += "file and printer sharing $(if ($wanted) { 'allowed' } else { 'blocked' }) on: $($profileList -join ', ')"

$Ansible.Result = @{
    state   = if ($wanted) { 'on' } else { 'off' }
    detail  = ($steps -join '; ')
}
$Ansible.Changed = $changed
