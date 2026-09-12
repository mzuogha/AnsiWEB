# AnsiWEB: find an installed application in the registry and uninstall it quietly.
# With -Apply:$false it only reports what it would do, changing nothing.
param(
    [string]$Pattern = '',
    [bool]$Apply = $false
)

$ErrorActionPreference = 'Stop'
if (-not $Pattern) { throw 'No detection pattern was given.' }

$keys = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$found = @(Get-ItemProperty -Path $keys -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -and -not $_.SystemComponent -and $_.DisplayName -match $Pattern })

if (-not $found) {
    $Ansible.Result = @{ matched = 0; removed = 0; state = 'not installed'; items = @(); detail = 'nothing matched' }
    $Ansible.Changed = $false
    return
}

$items = @()
$removed = 0
$skipped = @()

foreach ($app in $found) {
    $name = [string]$app.DisplayName
    $version = [string]$app.DisplayVersion
    $code = $app.PSChildName          # the {GUID} for MSI packages
    $command = $null
    $arguments = $null

    if ($code -match '^\{[0-9A-Fa-f-]{36}\}$') {
        $command = "$env:SystemRoot\System32\msiexec.exe"
        $arguments = @('/x', $code, '/qn', '/norestart')
    } elseif ($app.QuietUninstallString) {
        # Vendor-supplied silent command; run it through cmd so quoting survives
        $command = "$env:SystemRoot\System32\cmd.exe"
        $arguments = @('/c', [string]$app.QuietUninstallString)
    } else {
        # An interactive-only uninstaller would hang forever with nobody at the PC
        $skipped += "$name has no silent uninstall command"
        $items += @{ name = $name; version = $version; action = 'skipped - no silent uninstaller' }
        continue
    }

    if (-not $Apply) {
        $items += @{ name = $name; version = $version
                     action = "would run: $command $($arguments -join ' ')" }
        continue
    }

    $proc = Start-Process -FilePath $command -ArgumentList $arguments -Wait -PassThru -NoNewWindow
    $rc = $proc.ExitCode
    # 0 = done, 3010 = done but needs a restart, 1605 = it was not installed after all
    if ($rc -eq 0 -or $rc -eq 3010 -or $rc -eq 1605) {
        $removed++
        $items += @{ name = $name; version = $version; action = "removed (exit $rc)" }
    } else {
        $items += @{ name = $name; version = $version; action = "FAILED (exit $rc)" }
        $skipped += "$name failed with exit code $rc"
    }
}

$reboot = $items | Where-Object { $_.action -like '*3010*' }
$Ansible.Result = @{
    matched  = $found.Count
    removed  = $removed
    reboot   = [bool]$reboot
    state    = if (-not $Apply) { 'preview' } elseif ($removed -eq $found.Count) { 'removed' } else { 'partly removed' }
    items    = $items
    detail   = (($items | ForEach-Object { "$($_.name) $($_.version): $($_.action)" }) -join '; ')
}
$Ansible.Changed = ($Apply -and $removed -gt 0)

if ($Apply -and $skipped) { throw ($skipped -join '; ') }
