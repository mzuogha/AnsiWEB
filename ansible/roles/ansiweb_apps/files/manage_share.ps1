# AnsiWEB: make sure a shared folder exists on this PC, or remove the share.
param(
    [string]$Name = '',
    [string]$Path = '',
    [string]$Description = '',
    [string]$ReadAccess = '',     # comma-separated accounts
    [string]$ChangeAccess = '',
    [string]$FullAccess = '',
    [bool]$Remove = $false
)

$ErrorActionPreference = 'Stop'
if (-not $Name) { throw 'No share name was given.' }
$steps = @()
$changed = $false

$existing = Get-SmbShare -Name $Name -ErrorAction SilentlyContinue

if ($Remove) {
    if ($existing) {
        Remove-SmbShare -Name $Name -Force
        $steps += 'share removed (the folder and its contents are left alone)'
        $changed = $true
    } else {
        $steps += 'share was not there'
    }
    $Ansible.Result = @{ state = 'removed'; path = ''; detail = ($steps -join '; ') }
    $Ansible.Changed = $changed
    return
}

if (-not $Path) { throw 'No folder path was given.' }

# --- the folder itself -----------------------------------------------------
if (-not (Test-Path -LiteralPath $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    $steps += "created folder $Path"
    $changed = $true
}

# --- the share -------------------------------------------------------------
if (-not $existing) {
    New-SmbShare -Name $Name -Path $Path -Description $Description | Out-Null
    $steps += "shared as \\$env:COMPUTERNAME\$Name"
    $changed = $true
} else {
    if ($existing.Path -ne $Path) {
        # A share cannot be repointed, so replace it
        Remove-SmbShare -Name $Name -Force
        New-SmbShare -Name $Name -Path $Path -Description $Description | Out-Null
        $steps += "share repointed to $Path"
        $changed = $true
    } elseif ($existing.Description -ne $Description) {
        Set-SmbShare -Name $Name -Description $Description -Force
        $steps += 'description updated'
        $changed = $true
    }
}

# --- who may use it --------------------------------------------------------
function Split-Accounts([string]$Text) {
    @($Text -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

$wanted = @{}
foreach ($account in Split-Accounts $ReadAccess)   { $wanted[$account] = 'Read' }
foreach ($account in Split-Accounts $ChangeAccess) { $wanted[$account] = 'Change' }
foreach ($account in Split-Accounts $FullAccess)   { $wanted[$account] = 'Full' }

if ($wanted.Count) {
    $current = @(Get-SmbShareAccess -Name $Name -ErrorAction SilentlyContinue)
    foreach ($account in $wanted.Keys) {
        $have = $current | Where-Object { $_.AccountName -eq $account -and $_.AccessRight -eq $wanted[$account] -and $_.AccessControlType -eq 'Allow' }
        if (-not $have) {
            Revoke-SmbShareAccess -Name $Name -AccountName $account -Force -ErrorAction SilentlyContinue | Out-Null
            Grant-SmbShareAccess -Name $Name -AccountName $account -AccessRight $wanted[$account] -Force | Out-Null
            $steps += "$account = $($wanted[$account])"
            $changed = $true
        }
    }
    # Anyone granted by hand who is not in the list loses access, so the share
    # matches what AnsiWEB says it should be.
    foreach ($entry in $current) {
        if (-not $wanted.ContainsKey($entry.AccountName) -and $entry.AccountName -ne 'CREATOR OWNER') {
            Revoke-SmbShareAccess -Name $Name -AccountName $entry.AccountName -Force | Out-Null
            $steps += "removed $($entry.AccountName)"
            $changed = $true
        }
    }
}

if (-not $steps) { $steps += 'already set up' }

$Ansible.Result = @{
    state  = 'ready'
    path   = "\\$env:COMPUTERNAME\$Name"
    detail = ($steps -join '; ')
}
$Ansible.Changed = $changed
