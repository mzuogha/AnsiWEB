# AnsiWEB: add every .inf in an unpacked driver package to the Windows driver store.
param([string]$Folder)

$infs = @(Get-ChildItem -LiteralPath $Folder -Filter *.inf -Recurse -File)
if (-not $infs) { throw "No .inf file was found in the uploaded driver package." }

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
        default { throw "pnputil failed for $($inf.Name) with exit code $code`n$($output -join [Environment]::NewLine)" }
    }
}

$Ansible.Result = @{
    added   = $added
    skipped = $skipped
    reboot  = $reboot
    detail  = "$added of $($infs.Count) driver file(s) added" + $(if ($reboot) { ", reboot required" } else { "" })
}
$Ansible.Changed = $changed
