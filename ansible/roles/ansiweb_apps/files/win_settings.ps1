# AnsiWEB: a few common Windows settings, applied per PC.
# Every one is reversible from the same page, and anything not chosen is left
# exactly as it is - "leave alone" is a real option, not a hidden default.
param([string]$SettingsJson = '{}')

$ErrorActionPreference = 'Stop'
$want = $SettingsJson | ConvertFrom-Json
$done = @()

function Set-Reg($path, $name, $value, $kind, $label) {
    if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null }
    $current = (Get-ItemProperty -Path $path -Name $name -ErrorAction SilentlyContinue).$name
    if ("$current" -eq "$value") { return }
    New-ItemProperty -Path $path -Name $name -Value $value -PropertyType $kind -Force | Out-Null
    $script:done += $label
}

$explorer = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer'
$advanced = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced'

if ($null -ne $want.file_extensions) {
    Set-Reg $advanced 'HideFileExt' ([int](-not $want.file_extensions)) 'DWord' `
        "file extensions $(if ($want.file_extensions) {'shown'} else {'hidden'})"
}
if ($null -ne $want.hidden_files) {
    Set-Reg $advanced 'Hidden' $(if ($want.hidden_files) { 1 } else { 2 }) 'DWord' `
        "hidden files $(if ($want.hidden_files) {'shown'} else {'hidden'})"
}
if ($null -ne $want.fast_startup) {
    Set-Reg 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' 'HiberbootEnabled' `
        ([int][bool]$want.fast_startup) 'DWord' `
        "fast startup $(if ($want.fast_startup) {'on'} else {'off'})"
}
if ($null -ne $want.remote_desktop) {
    Set-Reg 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' 'fDenyTSConnections' `
        ([int](-not $want.remote_desktop)) 'DWord' `
        "remote desktop $(if ($want.remote_desktop) {'allowed'} else {'blocked'})"
    if ($want.remote_desktop) {
        Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
    } else {
        Disable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
    }
}
if ($want.power_plan) {
    $plan = switch ($want.power_plan) {
        'balanced'    { 'SCHEME_BALANCED' }
        'performance' { 'SCHEME_MIN' }
        'saver'       { 'SCHEME_MAX' }
        default       { $null }
    }
    if ($plan) {
        & powercfg.exe /setactive $plan 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $done += "power plan set to $($want.power_plan)" }
    }
}
if ($null -ne $want.sleep_minutes_ac) {
    & powercfg.exe /change standby-timeout-ac ([int]$want.sleep_minutes_ac) 2>&1 | Out-Null
    & powercfg.exe /change monitor-timeout-ac ([int]$want.sleep_minutes_ac) 2>&1 | Out-Null
    $done += "sleeps after $($want.sleep_minutes_ac) minute(s) on mains"
}
if ($null -ne $want.lock_screen_timeout) {
    Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' `
        'InactivityTimeoutSecs' ([int]$want.lock_screen_timeout * 60) 'DWord' `
        "locks after $($want.lock_screen_timeout) minute(s) idle"
}

$Ansible.Result = @{ changed_settings = $done; detail = ($done -join '; ') }
$Ansible.Changed = ($done.Count -gt 0)
