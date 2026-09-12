# AnsiWEB: list every program Windows shows under "Installed apps", so the
# server can keep a searchable inventory across all PCs.
param([int]$Limit = 600)

$keys = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)

$seen = @{}
$items = [System.Collections.Generic.List[object]]::new()

foreach ($entry in (Get-ItemProperty -Path $keys -ErrorAction SilentlyContinue)) {
    if (-not $entry.DisplayName) { continue }
    if ($entry.SystemComponent -eq 1) { continue }
    # Updates and hotfixes would swamp the list
    if ($entry.PSChildName -match '^KB\d{6,}$') { continue }
    if ($entry.ParentKeyName) { continue }

    $name = ([string]$entry.DisplayName).Trim()
    $version = ([string]$entry.DisplayVersion).Trim()
    $key = "$name|$version"
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true

    $installed = ''
    if ($entry.InstallDate -match '^\d{8}$') {
        $installed = "{0}-{1}-{2}" -f $entry.InstallDate.Substring(0, 4),
                                      $entry.InstallDate.Substring(4, 2),
                                      $entry.InstallDate.Substring(6, 2)
    }

    $items.Add([ordered]@{
        name      = $name
        version   = $version
        publisher = ([string]$entry.Publisher).Trim()
        installed = $installed
        arch      = if ($entry.PSPath -match 'WOW6432Node') { 'x86' } else { 'x64' }
    })
}

$sorted = @($items | Sort-Object { $_.name.ToLower() })
$Ansible.Result = @{
    count     = $sorted.Count
    truncated = ($sorted.Count -gt $Limit)
    apps      = @($sorted | Select-Object -First $Limit)
}
$Ansible.Changed = $false
