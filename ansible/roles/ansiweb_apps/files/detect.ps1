# AnsiWEB: compare installed apps (from the Windows "Installed apps" registry keys)
# with the versions in the AnsiWEB cache and decide what needs installing.
param(
    [string]$AppsJson = '[]'
)

$apps = @($AppsJson | ConvertFrom-Json)

# Depending on the PowerShell version and how the list reached us, the apps can
# arrive wrapped in another array, or as a single object. Unwrap one level and
# make sure what is left really is one entry per app: if it is not, every app
# collapses into one, every name runs together, and nothing installs.
while ($apps.Count -eq 1 -and ($apps[0] -is [array] -or $apps[0] -is [System.Collections.IList])) {
    $apps = @($apps[0])
}
# A single entry whose properties are themselves arrays means every app has
# been folded into one object - the names run together and nothing installs.
# Take it apart again rather than giving up.
if ($apps.Count -eq 1 -and $apps[0].id -is [array]) {
    $folded = $apps[0]
    $rebuilt = @()
    for ($i = 0; $i -lt $folded.id.Count; $i++) {
        $rebuilt += [pscustomobject]@{
            id        = $folded.id[$i]
            name      = @($folded.name)[$i]
            version   = @($folded.version)[$i]
            mode      = @($folded.mode)[$i]
            detect    = @($folded.detect)[$i]
        }
    }
    $apps = $rebuilt
}

foreach ($app in $apps) {
    if ($null -eq $app.id -or $app.id -is [array]) {
        throw ("The list of applications did not arrive as one entry per app. " +
               "AnsiWEB received $($apps.Count) entry/entries, the first with " +
               "id='$($app.id)' and name='$($app.name)'. Nothing has been changed on this PC.")
    }
}
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

$Ansible.Result = @($results)
$Ansible.Changed = $false
