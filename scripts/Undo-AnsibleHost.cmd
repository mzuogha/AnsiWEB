@echo off
rem ===========================================================================
rem  AnsiWEB: undo everything Prepare-AnsibleHost did to this PC.
rem
rem  Right-click this file and choose "Run as administrator".
rem
rem  It removes the WinRM listeners and firewall rule, the check-in tasks, the
rem  management account, and AnsiWEB's folder. It does NOT uninstall software
rem  AnsiWEB deployed - that is what Inventory & Uninstall is for - and it does
rem  not touch the PC's name, time zone or printers.
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

echo  [1/6] Stopping the check-in tasks
schtasks /delete /tn "AnsiWEB check-in" /f >>"%LOG%" 2>&1
schtasks /delete /tn "AnsiWEB check-in hourly" /f >>"%LOG%" 2>&1

echo  [2/6] Removing the WinRM listeners AnsiWEB created
winrm delete winrm/config/Listener?Address=*+Transport=HTTPS >>"%LOG%" 2>&1
winrm delete winrm/config/Listener?Address=*+Transport=HTTP >>"%LOG%" 2>&1

echo  [3/6] Removing the firewall rules
netsh advfirewall firewall delete rule name="AnsiWEB WinRM HTTPS" >>"%LOG%" 2>&1
netsh advfirewall firewall delete rule name="AnsiWEB WinRM" >>"%LOG%" 2>&1

echo  [4/6] Removing the self-signed certificate AnsiWEB made
powershell -NoProfile -Command "Get-ChildItem Cert:\LocalMachine\My | Where-Object { $_.Subject -eq \"CN=$env:COMPUTERNAME\" -and $_.Issuer -eq \"CN=$env:COMPUTERNAME\" } | Remove-Item" >>"%LOG%" 2>&1

echo  [5/6] Removing the management account "%ACCOUNT%"
net user "%ACCOUNT%" /delete >>"%LOG%" 2>&1
if errorlevel 1 (
    echo        no such account, or it is in use - nothing removed
) else (
    echo        removed
)

echo  [6/6] Clearing AnsiWEB's working folder
rmdir /s /q "%ProgramData%\AnsiWEB\cache" >>"%LOG%" 2>&1
rmdir /s /q "%ProgramData%\AnsiWEB\state" >>"%LOG%" 2>&1

echo.
echo  Done. This PC will no longer answer AnsiWEB, and will stop reporting in.
echo.
echo  Still in place, deliberately:
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
