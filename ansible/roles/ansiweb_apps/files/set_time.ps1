# AnsiWEB: set the time zone, point Windows at the right time source, and resync.
param(
    [string]$TimeZone = '',
    [string]$NtpServers = '',     # comma-separated
    [bool]$SyncNow = $true,
    [string]$ServerTime = ''      # AnsiWEB server's UTC time, for the drift report
)

$ErrorActionPreference = 'Stop'
$steps = @()
$changed = $false
$warnings = @()

# --- time zone -------------------------------------------------------------
$currentTz = (Get-TimeZone).Id
if ($TimeZone -and $TimeZone -ne $currentTz) {
    $known = Get-TimeZone -ListAvailable | Where-Object Id -eq $TimeZone
    if (-not $known) { throw "'$TimeZone' is not a time zone this PC recognises. Run 'tzutil /l' to list them." }
    Set-TimeZone -Id $TimeZone
    $steps += "time zone $currentTz -> $TimeZone"
    $changed = $true
} elseif ($TimeZone) {
    $steps += "time zone already $TimeZone"
}

# --- time source -----------------------------------------------------------
$servers = @($NtpServers -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($servers) {
    # 0x9 = send requests as a client, at the configured interval
    $peers = ($servers | ForEach-Object { "$_,0x9" }) -join ' '
    $before = (& w32tm /query /source 2>&1) -join ''

    $svc = Get-Service w32time
    if ($svc.StartType -ne 'Automatic') { Set-Service w32time -StartupType Automatic; $changed = $true }
    if ($svc.Status -ne 'Running') { Start-Service w32time; $changed = $true }

    $out = & w32tm /config "/manualpeerlist:$peers" /syncfromflags:manual /update 2>&1
    if ($LASTEXITCODE -ne 0) { throw "w32tm /config failed: $($out -join ' ')" }
    $steps += "time source set to $($servers -join ', ')"
    $changed = $true

    $after = (& w32tm /query /source 2>&1) -join ''
    if ($before -ne $after) { $steps += "source $before -> $after" }
}

# --- resync ----------------------------------------------------------------
if ($SyncNow) {
    $out = & w32tm /resync /force 2>&1
    if ($LASTEXITCODE -eq 0) {
        $steps += 'clock resynchronised'
        $changed = $true
    } else {
        # A PC with no reachable time source should be reported, not fail the run
        $warnings += "resync did not succeed: $(($out -join ' ').Trim())"
    }
}

# --- how far off is this PC? ----------------------------------------------
$drift = $null
if ($ServerTime) {
    try {
        $ref = [datetime]::Parse($ServerTime, [Globalization.CultureInfo]::InvariantCulture,
                                 [Globalization.DateTimeStyles]::AssumeUniversal -bor
                                 [Globalization.DateTimeStyles]::AdjustToUniversal)
        $drift = [math]::Round(((Get-Date).ToUniversalTime() - $ref).TotalSeconds, 1)
    } catch {
        $warnings += "could not compare with the server clock: $($_.Exception.Message)"
    }
}

$detail = $steps -join '; '
if ($warnings) { $detail = ($detail, ($warnings -join '; ') | Where-Object { $_ }) -join ' - ' }

$Ansible.Result = @{
    timezone   = (Get-TimeZone).Id
    local_time = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    source     = ((& w32tm /query /source 2>&1) -join '').Trim()
    drift      = $drift
    warnings   = $warnings
    detail     = if ($detail) { $detail } else { 'nothing to change' }
}
$Ansible.Changed = $changed
