# AnsiWEB: make sure a printer is set up on this PC, or remove it.
# Handles two kinds:
#   tcpip  - a network printer with its own IP: creates the port, then the printer
#   shared - a queue on a print server: connects to \\server\queue
param(
    [string]$Name = '',
    [string]$Kind = 'tcpip',
    [string]$DriverName = '',
    [string]$HostAddress = '',
    [int]$PortNumber = 9100,
    [string]$PortName = '',
    [string]$ConnectionName = '',
    [string]$Comment = '',
    [string]$Location = '',
    [bool]$SetDefault = $false,
    [bool]$Remove = $false
)

$ErrorActionPreference = 'Stop'
if (-not $Name) { throw 'No printer name was given.' }
$steps = @()
$changed = $false

function Get-ThisPrinter {
    Get-Printer -Name $Name -ErrorAction SilentlyContinue
}

# --- removal ---------------------------------------------------------------
if ($Remove) {
    if (Get-ThisPrinter) {
        Remove-Printer -Name $Name
        $steps += 'printer removed'
        $changed = $true
    } else {
        $steps += 'printer was not installed'
    }
    $Ansible.Result = @{ state = 'removed'; detail = ($steps -join '; '); default = $false }
    $Ansible.Changed = $changed
    return
}

# --- shared queue on a print server ---------------------------------------
if ($Kind -eq 'shared') {
    if (-not $ConnectionName) { throw 'A shared printer needs the \\server\queue path.' }
    $existing = Get-Printer -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq $ConnectionName -or $_.ShareName -eq $Name }
    if (-not $existing) {
        Add-Printer -ConnectionName $ConnectionName
        $steps += "connected to $ConnectionName"
        $changed = $true
    } else {
        $steps += 'already connected'
    }
} else {
    # --- printer with its own IP address ----------------------------------
    if (-not $HostAddress) { throw 'A network printer needs an IP address or host name.' }
    if (-not $DriverName) { throw 'A network printer needs a driver name.' }
    $port = if ($PortName) { $PortName } else { "IP_$HostAddress" }

    if (-not (Get-PrinterPort -Name $port -ErrorAction SilentlyContinue)) {
        Add-PrinterPort -Name $port -PrinterHostAddress $HostAddress -PortNumber $PortNumber
        $steps += "created port $port to ${HostAddress}:${PortNumber}"
        $changed = $true
    }

    # The driver has to be in the PC's driver store already - upload the
    # vendor's driver package on the Drivers page if it is not.
    if (-not (Get-PrinterDriver -Name $DriverName -ErrorAction SilentlyContinue)) {
        try {
            Add-PrinterDriver -Name $DriverName
            $steps += "added driver '$DriverName'"
            $changed = $true
        } catch {
            throw ("The driver '$DriverName' is not available on this PC. Upload the vendor's " +
                   "driver package on the Drivers page and apply it first. ($($_.Exception.Message))")
        }
    }

    $existing = Get-ThisPrinter
    if (-not $existing) {
        Add-Printer -Name $Name -DriverName $DriverName -PortName $port
        $steps += 'printer created'
        $changed = $true
    } else {
        if ($existing.PortName -ne $port) {
            Set-Printer -Name $Name -PortName $port
            $steps += "port changed to $port"
            $changed = $true
        }
        if ($existing.DriverName -ne $DriverName) {
            Set-Printer -Name $Name -DriverName $DriverName
            $steps += "driver changed to $DriverName"
            $changed = $true
        }
        if (-not $steps) { $steps += 'already set up' }
    }

    if ($Comment -or $Location) {
        Set-Printer -Name $Name -Comment $Comment -Location $Location
    }
}

# --- default printer -------------------------------------------------------
$isDefault = $false
if ($SetDefault) {
    $target = if ($Kind -eq 'shared') { $ConnectionName } else { $Name }
    $current = (Get-CimInstance Win32_Printer | Where-Object Default -eq $true).Name
    if ($current -ne $target) {
        $p = Get-CimInstance Win32_Printer -Filter "Name='$($target -replace "'", "''" -replace '\\', '\\\\')'"
        if ($p) {
            Invoke-CimMethod -InputObject $p -MethodName SetDefaultPrinter | Out-Null
            $steps += 'set as the default printer'
            $changed = $true
        }
    }
    $isDefault = $true
}

$Ansible.Result = @{
    state   = 'ready'
    default = $isDefault
    detail  = ($steps -join '; ')
}
$Ansible.Changed = $changed
