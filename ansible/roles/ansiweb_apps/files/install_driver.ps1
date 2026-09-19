# AnsiWEB: add a driver package's .inf files to the Windows driver store.
#
# A vendor package often holds several .inf files, and not all of them are
# signed drivers: some are installer stubs or lists that the catalogue does not
# cover, and Windows rejects those as tampered. That is normal and not a reason
# to abandon the package, so each .inf is handled on its own and the ones
# Windows will not take are reported rather than fatal - unless Strict is set.
param(
    [string]$Folder,
    [bool]$Strict = $false
)

$ErrorActionPreference = 'Stop'

$all = @(Get-ChildItem -LiteralPath $Folder -Filter *.inf -Recurse -File)
if (-not $all) { throw "No .inf file was found in the uploaded driver package." }

# Vendor archives often carry the same files twice (an extracted copy beside the
# original). Importing a file once is enough.
$infs = @()
$seen = @{}
foreach ($inf in ($all | Sort-Object FullName)) {
    $key = "$($inf.Name.ToLower())|$($inf.Length)"
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true
    $infs += $inf
}

$added = 0
$present = 0
$reboot = $false
$changed = $false
$rejected = @()
$failed = @()

foreach ($inf in $infs) {
    $output = & pnputil.exe /add-driver $inf.FullName /install 2>&1
    $code = $LASTEXITCODE
    $text = ($output -join ' ')

    if ($code -eq 0)    { $added++;   $changed = $true; continue }
    if ($code -eq 3010) { $added++;   $changed = $true; $reboot = $true; continue }
    if ($code -eq 259)  { $present++; continue }

    if ($text -match 'not present in the specified catalog|hash for the file|tampered|signature') {
        # Not a signed driver .inf, or one this Windows build's catalogue does
        # not cover. Windows will not take it; the rest of the package still works.
        $rejected += $inf.Name
        continue
    }
    $failed += "$($inf.Name): exit $code - $($text.Trim())"
}

if ($failed) {
    throw ("Windows could not install part of this driver package:`n  " +
           ($failed -join "`n  "))
}

if ($added -eq 0 -and $present -eq 0) {
    throw ("Windows rejected every .inf in this package: $($rejected -join ', ').`n" +
           "  Each one's file hash is missing from the package's signature catalogue, so Windows " +
           "treats them as altered.`n" +
           "  Usually that means the .zip is partial or was re-made. Download the driver from the " +
           "vendor again and upload their archive unchanged.")
}

if ($Strict -and $rejected) {
    throw ("$($rejected.Count) file(s) in this package were rejected by Windows: " +
           "$($rejected -join ', ').`n  $added driver(s) did install. Untick 'Stop if Windows " +
           "rejects any file' on this driver to accept that and carry on.")
}

$detail = "$added driver(s) added"
if ($present)  { $detail += ", $present already present" }
if ($rejected) { $detail += ", $($rejected.Count) file(s) not signed drivers and skipped ($($rejected -join ', '))" }
if ($reboot)   { $detail += ", reboot required" }

$Ansible.Result = @{
    added    = $added
    present  = $present
    rejected = $rejected
    reboot   = $reboot
    detail   = $detail
}
$Ansible.Changed = $changed
