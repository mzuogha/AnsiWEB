# AnsiWEB: read a PC's condition. Changes nothing.
$ErrorActionPreference = 'Stop'

$os = Get-CimInstance Win32_OperatingSystem
$sys = Get-CimInstance Win32_ComputerSystem
$disks = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType = 3" | ForEach-Object {
    [ordered]@{
        drive    = $_.DeviceID
        total_gb = [math]::Round($_.Size / 1GB, 1)
        free_gb  = [math]::Round($_.FreeSpace / 1GB, 1)
        free_pct = if ($_.Size) { [math]::Round(($_.FreeSpace / $_.Size) * 100, 1) } else { 0 }
    }
})

# A pending reboot leaves updates and installers half-done, so it is worth knowing
$rebootKeys = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
)
$rebootPending = $false
foreach ($k in $rebootKeys) { if (Test-Path $k) { $rebootPending = $true } }
$rename = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName' -ErrorAction SilentlyContinue
$target = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName' -ErrorAction SilentlyContinue
if ($rename -and $target -and $rename.ComputerName -ne $target.ComputerName) { $rebootPending = $true }

$lastBoot = $os.LastBootUpTime
$uptimeDays = [math]::Round(((Get-Date) - $lastBoot).TotalDays, 1)

$defender = $null
try {
    $mp = Get-MpComputerStatus -ErrorAction Stop
    $defender = [ordered]@{
        realtime   = [bool]$mp.RealTimeProtectionEnabled
        signatures = "$($mp.AntivirusSignatureLastUpdated)"
        age_days   = [math]::Round(((Get-Date) - $mp.AntivirusSignatureLastUpdated).TotalDays, 1)
    }
} catch { $defender = $null }   # not Defender, or the module is absent

$pending = @(Get-ChildItem 'C:\ProgramData\AnsiWEB\cache' -File -ErrorAction SilentlyContinue).Count

$Ansible.Result = @{
    os             = $os.Caption
    build          = $os.BuildNumber
    model          = "$($sys.Manufacturer) $($sys.Model)".Trim()
    memory_gb      = [math]::Round($sys.TotalPhysicalMemory / 1GB, 1)
    disks          = $disks
    reboot_pending = $rebootPending
    last_boot      = "$lastBoot"
    uptime_days    = $uptimeDays
    defender       = $defender
    leftover_files = $pending
}
$Ansible.Changed = $false
