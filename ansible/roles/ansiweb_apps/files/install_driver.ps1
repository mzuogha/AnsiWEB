# AnsiWEB: add every .inf in an unpacked driver package to the Windows driver store.
param([string]$Folder)

$infs = @(Get-ChildItem -LiteralPath $Folder -Filter *.inf -Recurse -File)
if (-not $infs) { throw "No .inf file was found in the uploaded driver package." }

$cats = @(Get-ChildItem -LiteralPath $Folder -Filter *.cat -Recurse -File)

$added   = 0
$skipped = 0
$reboot  = $false
$changed = $false

foreach ($inf in $infs) {
    $output = & pnputil.exe /add-driver $inf.FullName /install 2>&1
    $code = $LASTEXITCODE
    switch ($code) {
        0       { $added++;   $changed = $true }
        3010    { $added++;   $changed = $true; $reboot = $true }   # needs a restart
        259     { $skipped++ }                                       # nothing new to add
        default {
            $text = ($output -join ' ')
            # Windows will not install a driver whose catalogue does not vouch
            # for the files, so say what actually causes that rather than
            # repeating pnputil's wording.
            if ($text -match 'not present in the specified catalog|hash for the file') {
                $why = "Windows will not accept '$($inf.Name)': its signature catalogue does not match the files. "
                if (-not $cats) {
                    $why += "The package contains no .cat file at all, so it cannot be verified. "
                } else {
                    $why += "The package has $($cats.Count) .cat file(s), so a file has most likely been " +
                            "changed or left out. "
                }
                $why += "Upload the vendor's driver folder exactly as it was extracted - every file, " +
                        "including the .cat and any subfolders, and nothing edited. Re-zipping only " +
                        "the .inf, or opening it in an editor, breaks the signature. If the vendor ships " +
                        "separate x86 and x64 folders, zip the one for the PCs' architecture."
                throw $why
            }
            throw "pnputil failed for $($inf.Name) with exit code $code`n$($output -join [Environment]::NewLine)"
        }
    }
}

$Ansible.Result = @{
    added   = $added
    skipped = $skipped
    reboot  = $reboot
    detail  = "$added of $($infs.Count) driver file(s) added" + $(if ($reboot) { ", reboot required" } else { "" })
}
$Ansible.Changed = $changed
