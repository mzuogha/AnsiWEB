# AnsiWEB: compare installed apps (from the Windows "Installed apps" registry keys)
# with the versions in the AnsiWEB cache and decide what needs installing.
param(
    [string]$AppsJson = '[]',
    [string]$Office = 'no',
    [string]$OfficeVersion = ''
)

$apps = @($AppsJson | ConvertFrom-Json)
$keys = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$entries = @(Get-ItemProperty -Path $keys -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -and -not $_.SystemComponent } |
    Select-Object DisplayName, DisplayVersion)

function Get-VersionParts([string]$Value) {
    return , @([regex]::Matches("$Value", '\d+') | ForEach-Object { [int64]$_.Value })
}

function Compare-AppVersion([string]$A, [string]$B) {
    $pa = Get-VersionParts $A
    $pb = Get-VersionParts $B
    $n = [Math]::Max($pa.Count, $pb.Count)
    for ($i = 0; $i -lt $n; $i++) {
        $x = if ($i -lt $pa.Count) { $pa[$i] } else { 0 }
        $y = if ($i -lt $pb.Count) { $pb[$i] } else { 0 }
        if ($x -lt $y) { return -1 }
        if ($x -gt $y) { return 1 }
    }
    return 0
}

$results = [System.Collections.Generic.List[object]]::new()
foreach ($app in $apps) {
    $installed = $null
    foreach ($e in $entries) {
        if ($e.DisplayName -match $app.detect_pattern) {
            $v = if ($e.DisplayVersion) { [string]$e.DisplayVersion } else { '0' }
            if ($null -eq $installed -or (Compare-AppVersion $v $installed) -gt 0) { $installed = $v }
        }
    }
    $needed = $false
    $mismatch = $false
    if ($null -eq $installed) {
        $needed = $true
        $reason = 'not installed'
    } elseif ($app.mode -eq 'pinned') {
        if ((Compare-AppVersion $installed $app.version) -ne 0) {
            $mismatch = $true
            $reason = "pinned to $($app.version) but $installed is installed (left unchanged)"
        } else {
            $reason = 'pinned version installed'
        }
    } elseif ((Compare-AppVersion $installed $app.version) -lt 0) {
        $needed = $true
        $reason = "update $installed -> $($app.version)"
    } else {
        $reason = 'up to date'
    }
    $results.Add([ordered]@{
        id = $app.id; name = $app.name; installed = $installed; target = $app.version
        needed = $needed; mismatch = $mismatch; reason = $reason
        summary = "$($app.name): $reason"
    })
}

if ($Office -eq 'yes') {
    $c2r = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration' -ErrorAction SilentlyContinue
    $word = Test-Path -LiteralPath 'C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE'
    $ver = if ($c2r) { [string]$c2r.VersionToReport } else { $null }
    if (-not $word) { $reason = 'not installed'; $needed = $true }
    elseif ($ver -and $OfficeVersion -and (Compare-AppVersion $ver $OfficeVersion) -lt 0) {
        $reason = "installed $ver; Office updates itself to $OfficeVersion from AnsiWEB"; $needed = $false
    } else { $reason = 'installed'; $needed = $false }
    $results.Add([ordered]@{
        id = '__office__'; name = 'Microsoft Office'; installed = $ver; target = $OfficeVersion
        needed = $needed; mismatch = $false; reason = $reason; summary = "Microsoft Office: $reason"
    })
}

$Ansible.Result = @($results)
$Ansible.Changed = $false
