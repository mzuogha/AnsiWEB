<#
.SYNOPSIS
    One-time preparation of a Windows PC for AnsiWEB management.

.DESCRIPTION
    Run this once on each PC, from an elevated PowerShell window. It:
      - creates (or updates) the local administrator account AnsiWEB signs in with
      - lets that local account use its admin rights over the network
      - enables WinRM and creates an HTTPS listener with a self-signed certificate
      - opens TCP 5986 to the AnsiWEB server only, and closes plain-HTTP WinRM
      - checks its own work and writes a log
    It is safe to run again: everything it does is checked first.

.EXAMPLE
    # Downloaded from the AnsiWEB PCs page, so the server address and account
    # name are already filled in:
    powershell -ExecutionPolicy Bypass -File .\Prepare-AnsibleHost.ps1

.EXAMPLE
    # Or give them yourself:
    powershell -ExecutionPolicy Bypass -File .\Prepare-AnsibleHost.ps1 `
        -ControlNodeIP 192.168.1.10 -AccountName Admin
#>
[CmdletBinding()]
param(
    # Filled in when downloaded from the AnsiWEB PCs page
    [string]$ControlNodeIP = '__CONTROL_NODE_IP__',
    [string]$AccountName = '__ACCOUNT_NAME__',
    [securestring]$Password,
    [string]$LogPath = "$env:ProgramData\AnsiWEB\prepare-log.txt",
    # The window closes the moment the script ends when it is started by
    # double-clicking or "Run with PowerShell", taking any message with it.
    [switch]$NoPause
)

Set-StrictMode -Version Latest       # an undefined variable is a mistake, not a blank
$ErrorActionPreference = 'Stop'

$ADMINS_SID = 'S-1-5-32-544'         # built-in Administrators, on any Windows language
$WINRM_PORT = 5986
$FIREWALL_RULE = 'AnsiWEB WinRM HTTPS'

# --- logging ---------------------------------------------------------------
New-Item -ItemType Directory -Path (Split-Path $LogPath) -Force | Out-Null
try { Start-Transcript -Path $LogPath -Append | Out-Null } catch { }

function Hold {
    if (-not $NoPause) {
        Write-Host ""
        Read-Host "Press Enter to close this window" | Out-Null
    }
}

function Say([string]$Text, [string]$Colour = 'Gray') { Write-Host "  $Text" -ForegroundColor $Colour }

function Step {
    <# Run one step, and say which one failed rather than just how. #>
    param([string]$Name, [scriptblock]$Action)
    Write-Host "$Name..." -ForegroundColor Cyan
    try {
        & $Action
    } catch {
        Write-Host ""
        Write-Host "FAILED at: $Name" -ForegroundColor Red
        Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "  A full log is at $LogPath" -ForegroundColor Red
        try { Stop-Transcript | Out-Null } catch { }
        Hold
        exit 1
    }
}

# --- checks before anything is changed -------------------------------------
function Stop-Here([string]$Message) {
    Write-Host ""
    Write-Host $Message -ForegroundColor Red
    Hold
    exit 1
}

if ($PSVersionTable.PSVersion.Major -lt 5) {
    Stop-Here "This needs PowerShell 5.1 or newer; this PC has $($PSVersionTable.PSVersion)."
}
if (-not $ControlNodeIP -or $ControlNodeIP -eq ('__CONTROL' + '_NODE_IP__')) {
    Stop-Here 'No AnsiWEB server address in this file. Download it again from the PCs page, or pass -ControlNodeIP 192.168.1.10.'
}
if (-not $AccountName -or $AccountName -eq ('__ACCOUNT' + '_NAME__')) { $AccountName = 'Admin' }

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Stop-Here 'Run this from an elevated PowerShell window (right-click PowerShell, Run as administrator).'
}
if (-not $Password) {
    $Password = Read-Host -AsSecureString "Password for the local '$AccountName' account (the same one entered in AnsiWEB)"
}
if ($Password.Length -eq 0) { Stop-Here 'No password was given.' }

Write-Host ""
Write-Host "Preparing $env:COMPUTERNAME for AnsiWEB at $ControlNodeIP" -ForegroundColor Cyan
Write-Host ""

# --- 1. the account AnsiWEB signs in with ----------------------------------
Step "Local administrator account '$AccountName'" {
    $user = Get-LocalUser -Name $AccountName -ErrorAction SilentlyContinue
    if ($user) {
        Set-LocalUser -Name $AccountName -Password $Password -PasswordNeverExpires $true
        if (-not $user.Enabled) { Enable-LocalUser -Name $AccountName }
        Say "password updated on the existing account"
    } else {
        New-LocalUser -Name $AccountName -Password $Password -PasswordNeverExpires `
            -AccountNeverExpires -Description 'AnsiWEB management account' | Out-Null
        Say "account created"
    }

    # Group membership by SID, so this works on non-English Windows. The member
    # list can contain SIDs that no longer resolve, which makes the cmdlet
    # noisy, so errors are collected rather than thrown.
    $members = @(Get-LocalGroupMember -SID $ADMINS_SID -ErrorAction SilentlyContinue)
    $already = $members | Where-Object { $_.Name -like "*\$AccountName" }
    if ($already) {
        Say "already an administrator"
    } else {
        Add-LocalGroupMember -SID $ADMINS_SID -Member $AccountName
        Say "added to the Administrators group"
    }
}

# --- 2. let that local account work over the network -----------------------
Step 'Remote rights for local administrators' {
    # Without this, UAC strips the admin rights from a local account connecting
    # over the network and everything fails with "access denied".
    $path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
    New-ItemProperty -Path $path -Name 'LocalAccountTokenFilterPolicy' `
        -Value 1 -PropertyType DWord -Force | Out-Null
    Say 'LocalAccountTokenFilterPolicy set'
}

# --- 3. WinRM --------------------------------------------------------------
Step 'WinRM service' {
    # -SkipNetworkProfileCheck: otherwise Windows refuses on a "Public" network
    Enable-PSRemoting -Force -SkipNetworkProfileCheck | Out-Null
    Set-Service -Name WinRM -StartupType Automatic
    if ((Get-Service WinRM).Status -ne 'Running') { Start-Service WinRM }
    Say 'enabled and set to start automatically'
}

# --- 4. HTTPS listener with its own certificate ----------------------------
Step 'HTTPS listener' {
    foreach ($listener in @(Get-ChildItem WSMan:\localhost\Listener -ErrorAction SilentlyContinue)) {
        if ($listener.Keys -contains 'Transport=HTTPS') {
            Remove-Item -Path "WSMan:\localhost\Listener\$($listener.Name)" -Recurse -Force
            Say 'removed the previous HTTPS listener'
        }
    }

    # Names the PC may be reached by, so the certificate matches either way
    $names = @($env:COMPUTERNAME)
    try {
        $fqdn = [Net.Dns]::GetHostEntry($env:COMPUTERNAME).HostName
        if ($fqdn -and $names -notcontains $fqdn) { $names += $fqdn }
    } catch { }

    $cert = New-SelfSignedCertificate -DnsName $names `
        -CertStoreLocation 'Cert:\LocalMachine\My' `
        -NotAfter (Get-Date).AddYears(5) -FriendlyName 'AnsiWEB WinRM'

    New-Item -Path WSMan:\localhost\Listener -Transport HTTPS -Address * `
        -CertificateThumbPrint $cert.Thumbprint -Force | Out-Null
    Say "listening on $WINRM_PORT, certificate valid to $($cert.NotAfter.ToString('yyyy-MM-dd'))"
    Say "certificate names: $($names -join ', ')"
}

# --- 5. firewall -----------------------------------------------------------
Step 'Firewall' {
    Get-NetFirewallRule -DisplayName $FIREWALL_RULE -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $FIREWALL_RULE -Direction Inbound -Protocol TCP `
        -LocalPort $WINRM_PORT -RemoteAddress $ControlNodeIP -Action Allow -Profile Any | Out-Null
    Say "TCP $WINRM_PORT open to $ControlNodeIP only"

    $http = @(Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue)
    if ($http) {
        $http | Disable-NetFirewallRule
        Say 'plain-HTTP WinRM (5985) closed'
    }
}

# --- 6. check the result ---------------------------------------------------
$problems = @()

$members = @(Get-LocalGroupMember -SID $ADMINS_SID -ErrorAction SilentlyContinue)
if (-not ($members | Where-Object { $_.Name -like "*\$AccountName" })) {
    $problems += "$AccountName is not in the Administrators group"
}
if ((Get-Service WinRM).Status -ne 'Running') {
    $problems += 'the WinRM service is not running'
}
$https = @(Get-ChildItem WSMan:\localhost\Listener -ErrorAction SilentlyContinue |
           Where-Object { $_.Keys -contains 'Transport=HTTPS' })
if (-not $https) {
    $problems += 'there is no WinRM HTTPS listener'
}
if (-not (Get-NetFirewallRule -DisplayName $FIREWALL_RULE -ErrorAction SilentlyContinue)) {
    $problems += "the firewall rule for port $WINRM_PORT is missing"
}
# Is anything actually listening? This is a local socket check on purpose: a
# WinRM handshake to "localhost" would fail the certificate name check even
# when the PC is set up correctly, which is not a real problem.
$listening = $false
try {
    $listening = [bool](Get-NetTCPConnection -LocalPort $WINRM_PORT -State Listen -ErrorAction Stop)
} catch {
    try {
        $probe = New-Object Net.Sockets.TcpClient
        $probe.Connect('127.0.0.1', $WINRM_PORT)
        $listening = $probe.Connected
        $probe.Close()
    } catch { $listening = $false }
}
if (-not $listening) { $problems += "nothing is listening on port $WINRM_PORT" }

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
       Select-Object -First 1).IPAddress

Write-Host ""
if ($problems) {
    Write-Host 'Finished, but with problems:' -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host "  A full log is at $LogPath" -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch { }
    Hold
    exit 1
}

Write-Host 'Checked: account, WinRM service, HTTPS listener, firewall rule and port.' -ForegroundColor Green
Write-Host ''
Write-Host 'Done. Add this PC on the AnsiWEB PCs page:' -ForegroundColor Green
Write-Host "  PC name:    $env:COMPUTERNAME"
Write-Host "  IP address: $ip"
Write-Host ''
Write-Host "A log of this run is at $LogPath"
try { Stop-Transcript | Out-Null } catch { }
Hold
