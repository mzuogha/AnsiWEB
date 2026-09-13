<#
.SYNOPSIS
    One-time preparation of a Windows PC (workgroup, no AD) for AnsiWEB management.

.DESCRIPTION
    - Creates the local administrator account AnsiWEB uses (default: Admin)
    - Enables WinRM and allows local admin accounts to connect remotely
    - Creates a WinRM HTTPS listener with a self-signed certificate
    - Opens TCP 5986 ONLY to the Ansible control node's IP
    - Blocks the unencrypted WinRM HTTP port (5985) in the firewall
    Safe to run more than once.

.EXAMPLE
    # From an elevated PowerShell prompt (script downloaded from the AnsiWEB PCs page):
    powershell -ExecutionPolicy Bypass -File .\Prepare-AnsibleHost.ps1

    You are prompted for the account password. Use the SAME password on every PC,
    and the same one entered on the AnsiWEB PCs page.

.EXAMPLE
    # Different account name and server, without the download:
    powershell -ExecutionPolicy Bypass -File .\Prepare-AnsibleHost.ps1 -ControlNodeIP 192.168.1.10 -AccountName Admin
#>
[CmdletBinding()]
param(
    # Filled in automatically when downloaded from the AnsiWEB PCs page
    [string]$ControlNodeIP = '__CONTROL_NODE_IP__',

    # Filled in automatically when downloaded from the AnsiWEB PCs page
    [string]$AccountName = '__ACCOUNT_NAME__',

    [Parameter(Mandatory = $true)]
    [securestring]$Password
)

$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 or PowerShell 7 on Windows are both fine; anything
# older is missing the cmdlets used below.
if ($PSVersionTable.PSVersion.Major -lt 5) {
    throw "This needs PowerShell 5.1 or newer. This PC has $($PSVersionTable.PSVersion)."
}

function Step {
    param([string]$Name, [scriptblock]$Action)
    try {
        & $Action
    } catch {
        throw "Step '$Name' failed: $($_.Exception.Message)"
    }
}

if (-not $ControlNodeIP -or $ControlNodeIP -eq ('__CONTROL' + '_NODE_IP__')) {
    throw 'Specify the AnsiWEB server IP: -ControlNodeIP 192.168.1.10 (or download this script from the AnsiWEB PCs page).'
}
if (-not $AccountName -or $AccountName -eq ('__ACCOUNT' + '_NAME__')) {
    $AccountName = 'Admin'
}

# --- Must run as Administrator ---------------------------------------------
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated (Run as Administrator) PowerShell window.'
}

Write-Host "Preparing $env:COMPUTERNAME for Ansible..." -ForegroundColor Cyan

# --- 1. Local administrator account for Ansible ----------------------------
$user = Get-LocalUser -Name $AccountName -ErrorAction SilentlyContinue
if (-not $user) {
    New-LocalUser -Name $AccountName -Password $Password -PasswordNeverExpires `
        -AccountNeverExpires -Description 'AnsiWEB management account' | Out-Null
    Write-Host "  Created local account $AccountName"
} else {
    Set-LocalUser -Name $AccountName -Password $Password -PasswordNeverExpires $true
    Enable-LocalUser -Name $AccountName
    Write-Host "  Updated password for existing account $AccountName"
}

# S-1-5-32-544 = built-in Administrators group (works on any Windows language)
$adminGroup = Get-LocalGroup -SID 'S-1-5-32-544'
$isMember = Get-LocalGroupMember -Group $adminGroup -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "*\$AccountName" }
if (-not $isMember) {
    Add-LocalGroupMember -Group $adminGroup -Member $AccountName
    Write-Host "  Added $AccountName to $($adminGroup.Name)"
}

# --- 2. Enable WinRM -------------------------------------------------------
Enable-PSRemoting -Force -SkipNetworkProfileCheck | Out-Null
Set-Service -Name WinRM -StartupType Automatic
Write-Host '  WinRM enabled'

# --- 3. Let local admin accounts use full admin rights remotely ------------
# Without this, UAC strips admin rights from local accounts connecting over the network.
New-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' `
    -Name 'LocalAccountTokenFilterPolicy' -Value 1 -PropertyType DWord -Force | Out-Null

# --- 4. HTTPS listener with a self-signed certificate ----------------------
foreach ($listener in @(Get-ChildItem WSMan:\localhost\Listener -ErrorAction SilentlyContinue)) {
    if ($listener.Keys -contains 'Transport=HTTPS') {
        Remove-Item -Path "WSMan:\localhost\Listener\$($listener.Name)" -Recurse -Force
    }
}

$cert = New-SelfSignedCertificate -DnsName $env:COMPUTERNAME `
    -CertStoreLocation 'Cert:\LocalMachine\My' `
    -NotAfter (Get-Date).AddYears(5) `
    -FriendlyName 'Ansible WinRM'

New-Item -Path WSMan:\localhost\Listener -Transport HTTPS -Address * `
    -CertificateThumbPrint $cert.Thumbprint -Force | Out-Null
Write-Host "  HTTPS listener created (cert expires $($cert.NotAfter.ToString('yyyy-MM-dd')))"

# --- 5. Firewall -----------------------------------------------------------
Get-NetFirewallRule -DisplayName 'Ansible WinRM HTTPS' -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'Ansible WinRM HTTPS' -Direction Inbound -Protocol TCP `
    -LocalPort 5986 -RemoteAddress $ControlNodeIP -Action Allow -Profile Any | Out-Null

# Close the unencrypted HTTP port that Enable-PSRemoting opens
Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue |
    Disable-NetFirewallRule
Write-Host "  Firewall: 5986 open to $ControlNodeIP only; 5985 closed"

# --- 6. Summary line for the Ansible inventory -----------------------------
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -First 1).IPAddress

# --- 6. Check it actually worked ------------------------------------------
$problems = @()

if (-not (Get-LocalGroupMember -SID $adminSid -ErrorAction SilentlyContinue |
          Where-Object { $_.Name -like "*\$AccountName" })) {
    $problems += "$AccountName is not in the Administrators group"
}
if ((Get-Service WinRM).Status -ne 'Running') {
    $problems += 'the WinRM service is not running'
}
if (-not (@(Get-ChildItem WSMan:\localhost\Listener -ErrorAction SilentlyContinue) |
          Where-Object { $_.Keys -contains 'Transport=HTTPS' })) {
    $problems += 'there is no WinRM HTTPS listener'
}
if (-not (Get-NetFirewallRule -DisplayName 'Ansible WinRM HTTPS' -ErrorAction SilentlyContinue)) {
    $problems += 'the firewall rule for port 5986 is missing'
}
try {
    Test-WSMan -ComputerName localhost -UseSSL -ErrorAction Stop | Out-Null
} catch {
    $problems += "WinRM did not answer on HTTPS locally: $($_.Exception.Message)"
}

if ($problems) {
    Write-Host ''
    Write-Host 'Finished, but with problems:' -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host 'Fix these before adding the PC to AnsiWEB.' -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host 'Checked: account, WinRM service, HTTPS listener and firewall rule are all in place.'
Write-Host 'Done. Add this PC on the AnsiWEB PCs page:' -ForegroundColor Green
Write-Host "  PC name:    $env:COMPUTERNAME"
Write-Host "  IP address: $ip"
