# AnsiWEB: add every .inf in an unpacked driver package to the Windows driver store.
param(
    [string]$Folder,
    # Retry with DISM /ForceUnsigned when Windows rejects the package's signature.
    [bool]$AllowUnsigned = $false
)

$ErrorActionPreference = 'Stop'

$infs = @(Get-ChildItem -LiteralPath $Folder -Filter *.inf -Recurse -File)
if (-not $infs) { throw "No .inf file was found in the uploaded driver package." }
$cats = @(Get-ChildItem -LiteralPath $Folder -Filter *.cat -Recurse -File)

function Get-CatalogFor([System.IO.FileInfo]$Inf) {
    # An INF names its catalogue with CatalogFile= (sometimes per architecture).
    # Windows looks for that file beside the INF and nowhere else.
    $named = @()
    foreach ($line in Get-Content -LiteralPath $Inf.FullName -ErrorAction SilentlyContinue) {
        if ($line -match '^\s*CatalogFile(\.[A-Za-z0-9]+)?\s*=\s*(.+?)\s*$') {
            $named += $matches[2].Trim()
        }
    }
    $named = $named | Select-Object -Unique
    $beside = @()
    $elsewhere = @()
    foreach ($name in $named) {
        if (Test-Path -LiteralPath (Join-Path $Inf.DirectoryName $name)) {
            $beside += $name
        } else {
            $found = $cats | Where-Object { $_.Name -ieq $name } | Select-Object -First 1
            if ($found) { $elsewhere += "$name (found in $($found.Directory.Name))" }
            else { $elsewhere += "$name (not in the package)" }
        }
    }
    return @{ named = $named; beside = $beside; elsewhere = $elsewhere }
}

$added = 0
$skipped = 0
$unsigned = 0
$reboot = $false
$changed = $false
$notes = @()

foreach ($inf in $infs) {
    $output = & pnputil.exe /add-driver $inf.FullName /install 2>&1
    $code = $LASTEXITCODE
    # Note: "continue" inside a switch leaves the switch, not the loop, so these
    # are plain conditions.
    if ($code -eq 0)    { $added++;   $changed = $true; continue }
    if ($code -eq 3010) { $added++;   $changed = $true; $reboot = $true; continue }
    if ($code -eq 259)  { $skipped++; continue }

    $text = ($output -join ' ')
    $signature = $text -match 'not present in the specified catalog|hash for the file|signature'

    if ($signature -and $AllowUnsigned) {
        # DISM can add a package the signature check refuses. This is the switch
        # AnsiWEB offers for a vendor package Windows will not vouch for.
        $dism = & dism.exe /online /add-driver /driver:"$($inf.FullName)" /forceunsigned 2>&1
        $dismCode = $LASTEXITCODE
        if ($dismCode -eq 0 -or $dismCode -eq 3010) {
            $unsigned++
            $changed = $true
            if ($dismCode -eq 3010) { $reboot = $true }
            $notes += "$($inf.Name) installed with the signature check bypassed"
            continue
        }
        throw ("Windows refused '$($inf.Name)' even with the signature check bypassed. " +
               "DISM exit code $dismCode.`n" + ($dism -join [Environment]::NewLine))
    }

    if ($signature) {
        $cat = Get-CatalogFor $inf
        $why = "Windows will not accept '$($inf.Name)': its files do not match its signature catalogue.`n"
        if (-not $cat.named) {
            $why += "  The .inf names no CatalogFile, so Windows has nothing to verify it against.`n"
        } else {
            $why += "  The .inf expects: $($cat.named -join ', ')`n"
            if ($cat.beside) { $why += "  Present beside the .inf: $($cat.beside -join ', ')`n" }
            if ($cat.elsewhere) { $why += "  Missing from the .inf's own folder: $($cat.elsewhere -join '; ')`n" }
        }
        $why += "  The package holds $($cats.Count) .cat file(s) in total.`n"
        $why += "  Either a file differs from the one the vendor signed - a partial or re-made .zip, " +
                "a file opened and saved, or two versions mixed together - or the package is for a " +
                "different Windows version or architecture.`n"
        $why += "  If you trust this package, tick 'Install even if Windows rejects the signature' " +
                "on the driver in AnsiWEB and run the job again."
        throw $why
    }

    throw "pnputil failed for $($inf.Name) with exit code $code`n$($output -join [Environment]::NewLine)"
}

$detail = "$added of $($infs.Count) driver file(s) added"
if ($unsigned) { $detail += ", $unsigned with the signature check bypassed" }
if ($skipped)  { $detail += ", $skipped already present" }
if ($reboot)   { $detail += ", reboot required" }
if ($notes)    { $detail += " - " + ($notes -join '; ') }

$Ansible.Result = @{
    added    = $added
    skipped  = $skipped
    unsigned = $unsigned
    reboot   = $reboot
    detail   = $detail
}
$Ansible.Changed = $changed
