# AnsiWEB: make sure a network printer is set up on this PC, or remove it.
# The printer has its own IP address, so this creates the port, then the printer.
param(
    [string]$Name = '',
    [string]$DriverName = '',
    [string]$HostAddress = '',
    [int]$PortNumber = 9100,
    [string]$PortName = '',
    [string]$Comment = '',
    [string]$Location = '',
    [bool]$SetDefault = $false,
    [bool]$Remove = $false
)

$ErrorActionPreference = 'Stop'
if (-not $Name) { throw 'No printer name was given.' }

$steps = @()
$changed = $false
$existing = Get-Printer -Name $Name -ErrorAction SilentlyContinue

# --- removal ---------------------------------------------------------------
if ($Remove) {
    if ($existing) {
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

if (-not $HostAddress) { throw 'A network printer needs an IP address or host name.' }
if (-not $DriverName)  { throw 'A network printer needs a driver name.' }

# --- the port --------------------------------------------------------------
$port = if ($PortName) { $PortName } else { "IP_$HostAddress" }
if (-not (Get-PrinterPort -Name $port -ErrorAction SilentlyContinue)) {
    Add-PrinterPort -Name $port -PrinterHostAddress $HostAddress -PortNumber $PortNumber
    $steps += "created port $port to ${HostAddress}:${PortNumber}"
    $changed = $true
}

# --- the driver ------------------------------------------------------------
# AnsiWEB stages the vendor's driver package before this runs, so the driver is
# normally in the PC's driver store by now.
if (-not (Get-PrinterDriver -Name $DriverName -ErrorAction SilentlyContinue)) {
    try {
        Add-PrinterDriver -Name $DriverName
        $steps += "added driver '$DriverName'"
        $changed = $true
    } catch {
        throw ("The driver '$DriverName' is not available on this PC. Attach the vendor's driver " +
               "package to this printer in AnsiWEB, or check that the name matches the one in the " +
               "driver's .inf exactly. ($($_.Exception.Message))")
    }
}

# --- the printer -----------------------------------------------------------
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
}

if ($Comment -or $Location) {
    Set-Printer -Name $Name -Comment $Comment -Location $Location
}

# --- default printer -------------------------------------------------------
$isDefault = $false
if ($SetDefault) {
    $current = (Get-CimInstance Win32_Printer | Where-Object Default -eq $true).Name
    if ($current -ne $Name) {
        $escaped = $Name -replace "'", "''"
        $p = Get-CimInstance Win32_Printer -Filter "Name='$escaped'"
        if ($p) {
            Invoke-CimMethod -InputObject $p -MethodName SetDefaultPrinter | Out-Null
            $steps += 'set as the default printer'
            $changed = $true
        }
    }
    $isDefault = $true
}

if (-not $steps) { $steps += 'already set up' }

$Ansible.Result = @{
    state   = 'ready'
    default = $isDefault
    detail  = ($steps -join '; ')
}
$Ansible.Changed = $changed
