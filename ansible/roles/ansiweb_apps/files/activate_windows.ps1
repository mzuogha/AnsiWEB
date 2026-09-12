# AnsiWEB: install a Windows product key and activate, either against a KMS host
# or with a MAK key. Reports what it did without ever echoing the key.
param(
    [string]$Key = '',
    [string]$Mode = 'mak',
    [string]$KmsHost = '',
    [int]$KmsPort = 1688,
    [bool]$SkipIfActivated = $true
)

$ErrorActionPreference = 'Stop'
$WindowsAppId = '55c92734-d682-4d71-983e-d6ec3f16059f'   # Windows operating system

function Get-WindowsLicense {
    Get-CimInstance SoftwareLicensingProduct `
        -Filter "ApplicationId = '$WindowsAppId' AND PartialProductKey IS NOT NULL" `
        -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Get-StatusText([int]$Status) {
    switch ($Status) {
        0 { 'unlicensed' }
        1 { 'activated' }
        2 { 'in the grace period' }
        3 { 'out of the grace period' }
        4 { 'non-genuine' }
        5 { 'notification mode' }
        6 { 'in the extended grace period' }
        default { "status $Status" }
    }
}

$before = Get-WindowsLicense
$beforeStatus = if ($before) { [int]$before.LicenseStatus } else { 0 }
$edition = if ($before) { $before.Name } else { 'unknown edition' }

if ($SkipIfActivated -and $beforeStatus -eq 1) {
    $Ansible.Result = @{
        changed_state = $false
        before        = Get-StatusText $beforeStatus
        after         = Get-StatusText $beforeStatus
        edition       = $edition
        detail        = 'already activated, left alone'
    }
    $Ansible.Changed = $false
    return
}

$service = Get-CimInstance SoftwareLicensingService
$steps = @()

if ($Mode -eq 'kms') {
    if (-not $KmsHost) { throw 'KMS mode needs a KMS host name.' }
    if ($Key) {
        Invoke-CimMethod -InputObject $service -MethodName InstallProductKey `
            -Arguments @{ ProductKey = $Key } | Out-Null
        $steps += 'installed the product key'
    }
    Invoke-CimMethod -InputObject $service -MethodName SetKeyManagementServiceMachine `
        -Arguments @{ MachineName = $KmsHost } | Out-Null
    Invoke-CimMethod -InputObject $service -MethodName SetKeyManagementServicePort `
        -Arguments @{ PortNumber = [uint32]$KmsPort } | Out-Null
    $steps += "pointed activation at ${KmsHost}:${KmsPort}"
} else {
    if (-not $Key) { throw 'No product key was provided.' }
    Invoke-CimMethod -InputObject $service -MethodName InstallProductKey `
        -Arguments @{ ProductKey = $Key } | Out-Null
    $steps += 'installed the product key'
}

# Windows needs a moment before the new key shows up as the licensed product
Start-Sleep -Seconds 3
$product = Get-WindowsLicense
if (-not $product) { throw 'Windows did not accept the product key.' }

try {
    Invoke-CimMethod -InputObject $product -MethodName Activate | Out-Null
    $steps += 'requested activation'
} catch {
    throw "Activation failed: $($_.Exception.Message)"
}

Start-Sleep -Seconds 3
$after = Get-WindowsLicense
$afterStatus = if ($after) { [int]$after.LicenseStatus } else { 0 }

$Ansible.Result = @{
    changed_state = ($afterStatus -ne $beforeStatus)
    before        = Get-StatusText $beforeStatus
    after         = Get-StatusText $afterStatus
    edition       = if ($after) { $after.Name } else { $edition }
    detail        = ($steps -join '; ')
}
$Ansible.Changed = $true

if ($afterStatus -ne 1) {
    throw "Windows is still $(Get-StatusText $afterStatus) after activation ($($steps -join '; '))."
}
