@echo off
rem ===========================================================================
rem  AnsiWEB: undo everything Prepare-AnsibleHost did to this PC.
rem
rem  Right-click this file and choose "Run as administrator".
rem
rem  It removes the WinRM listeners and firewall rule, the check-in tasks and
rem  AnsiWEB's folder. It leaves the management account alone: it may be the
rem  only administrator on this PC, and removing it could lock you out. It does
rem  NOT uninstall software AnsiWEB deployed - that is what Inventory &
rem  Uninstall is for - and it does not touch the PC's name, time zone or
rem  printers.
rem ===========================================================================
setlocal
set "ACCOUNT=__ACCOUNT_NAME__"
set "LOG=%ProgramData%\AnsiWEB\undo-log.txt"
if not exist "%ProgramData%\AnsiWEB" mkdir "%ProgramData%\AnsiWEB" >nul 2>&1

net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo  This has to run as administrator. Right-click the file and choose
    echo  "Run as administrator", then try again.
    echo.
    pause
    exit /b 1
)

echo. & echo  Undoing the AnsiWEB preparation on %COMPUTERNAME% & echo.

echo  [1/5] Stopping the check-in tasks
schtasks /delete /tn "AnsiWEB check-in" /f >>"%LOG%" 2>&1
schtasks /delete /tn "AnsiWEB check-in hourly" /f >>"%LOG%" 2>&1

echo  [2/5] Removing the WinRM listeners AnsiWEB created
winrm delete winrm/config/Listener?Address=*+Transport=HTTPS >>"%LOG%" 2>&1
winrm delete winrm/config/Listener?Address=*+Transport=HTTP >>"%LOG%" 2>&1

echo  [3/5] Removing the firewall rules
netsh advfirewall firewall delete rule name="AnsiWEB WinRM HTTPS" >>"%LOG%" 2>&1
netsh advfirewall firewall delete rule name="AnsiWEB WinRM" >>"%LOG%" 2>&1

echo  [4/5] Removing the self-signed certificate AnsiWEB made
powershell -NoProfile -Command "Get-ChildItem Cert:\LocalMachine\My | Where-Object { $_.Subject -eq \"CN=$env:COMPUTERNAME\" -and $_.Issuer -eq \"CN=$env:COMPUTERNAME\" } | Remove-Item" >>"%LOG%" 2>&1

echo  [5/5] Clearing AnsiWEB's working folder
rmdir /s /q "%ProgramData%\AnsiWEB\cache" >>"%LOG%" 2>&1
rmdir /s /q "%ProgramData%\AnsiWEB\state" >>"%LOG%" 2>&1

echo.
echo  Done. This PC will no longer answer AnsiWEB, and will stop reporting in.
echo.
echo  Still in place, deliberately:
echo    - the "%ACCOUNT%" account. It is left alone because it may be the only
echo      administrator on this PC, and removing it could lock you out. Delete
echo      it yourself once you are sure another administrator works.
echo    - software AnsiWEB installed (remove it from Inventory ^& Uninstall,
echo      or from Windows, before running this if you want it gone)
echo    - the computer name, time zone and any printers that were set up
echo    - WinRM itself, if it was already switched on before AnsiWEB
echo.
echo  Remember to remove this PC from the PCs page in AnsiWEB as well.
echo.
echo  A log of what happened is in %LOG%
echo.
pause
