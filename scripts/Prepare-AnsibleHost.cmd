@echo off
setlocal enabledelayedexpansion
rem ===========================================================================
rem  AnsiWEB - simple PC preparation, using only built-in Windows commands.
rem
rem  This is the alternative to the PowerShell script: no certificates, no
rem  execution policy, nothing to unblock. AnsiWEB then connects over port 5985
rem  with NTLM, which encrypts the traffic at the message level.
rem
rem  Right-click this file and choose "Run as administrator".
rem ===========================================================================

set "SERVER=__CONTROL_NODE_IP__"
set "ACCOUNT=__ACCOUNT_NAME__"
set "PORT=5985"
set "RULE=AnsiWEB WinRM"
set "LOG=%ProgramData%\AnsiWEB\prepare-log.txt"

if not exist "%ProgramData%\AnsiWEB" mkdir "%ProgramData%\AnsiWEB" >nul 2>&1
echo. >> "%LOG%"
echo ==== %DATE% %TIME% ==== >> "%LOG%"

echo.
echo  Preparing %COMPUTERNAME% for AnsiWEB at %SERVER%
echo.

rem --- must be elevated ------------------------------------------------------
net session >nul 2>&1
if errorlevel 1 (
    echo  [X] This must be run as administrator.
    echo      Right-click the file and choose "Run as administrator".
    goto :finished
)

rem --- the server address must have been filled in ---------------------------
echo %SERVER% | find "__CONTROL" >nul
if not errorlevel 1 (
    echo  [X] No AnsiWEB server address in this file.
    echo      Download it again from the PCs page in AnsiWEB.
    goto :finished
)

rem --- 1. the account AnsiWEB signs in with ---------------------------------
echo  [1/5] Local administrator account "%ACCOUNT%"
net user "%ACCOUNT%" >nul 2>&1
if errorlevel 1 (
    echo        Enter a password for the new account ^(it is not shown^):
    net user "%ACCOUNT%" * /add /comment:"AnsiWEB management account" /passwordchg:no >>"%LOG%" 2>&1
    if errorlevel 1 goto :failed_account
    echo        account created
) else (
    echo        Account exists. Enter its password ^(it is not shown^):
    net user "%ACCOUNT%" * >>"%LOG%" 2>&1
    if errorlevel 1 goto :failed_account
    net user "%ACCOUNT%" /active:yes >>"%LOG%" 2>&1
    echo        password updated
)
wmic useraccount where "name='%ACCOUNT%'" set PasswordExpires=false >>"%LOG%" 2>&1

rem The Administrators group is not called "Administrators" on every language,
rem so look up its real name by its well-known SID.
set "ADMINS="
for /f "usebackq tokens=*" %%G in (`wmic group where "sid='S-1-5-32-544'" get name /value 2^>nul ^| find "Name="`) do (
    set "%%G"
    set "ADMINS=!Name!"
)
if not defined ADMINS set "ADMINS=Administrators"
net localgroup "%ADMINS%" | find /i "%ACCOUNT%" >nul
if errorlevel 1 (
    net localgroup "%ADMINS%" "%ACCOUNT%" /add >>"%LOG%" 2>&1
    if errorlevel 1 goto :failed_group
    echo        added to %ADMINS%
) else (
    echo        already in %ADMINS%
)

rem --- 2. let that local account work over the network ------------------------
echo  [2/5] Remote rights for local administrators
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" ^
    /v LocalAccountTokenFilterPolicy /t REG_DWORD /d 1 /f >>"%LOG%" 2>&1
if errorlevel 1 goto :failed_registry
echo        done

rem --- 3. WinRM --------------------------------------------------------------
echo  [3/5] WinRM service
sc config WinRM start= auto >>"%LOG%" 2>&1
net start WinRM >>"%LOG%" 2>&1
call winrm quickconfig -quiet -force >>"%LOG%" 2>&1
if errorlevel 1 (
    rem Older Windows has no -force; try without it
    call winrm quickconfig -quiet >>"%LOG%" 2>&1
)
rem Refuse unencrypted traffic: NTLM encrypts each message, so this stays off.
call winrm set winrm/config/service @{AllowUnencrypted="false"} >>"%LOG%" 2>&1
echo        enabled, unencrypted traffic refused

rem --- 4. firewall -----------------------------------------------------------
echo  [4/5] Firewall
netsh advfirewall firewall delete rule name="%RULE%" >>"%LOG%" 2>&1
netsh advfirewall firewall add rule name="%RULE%" dir=in action=allow ^
    protocol=TCP localport=%PORT% remoteip=%SERVER% >>"%LOG%" 2>&1
if errorlevel 1 goto :failed_firewall
echo        TCP %PORT% open to %SERVER% only

rem --- 5. check the result ---------------------------------------------------
echo  [5/5] Checking
set "PROBLEM="
net localgroup "%ADMINS%" | find /i "%ACCOUNT%" >nul || set "PROBLEM=!PROBLEM! account-not-admin"
sc query WinRM | find "RUNNING" >nul || set "PROBLEM=!PROBLEM! winrm-not-running"
netstat -an | find ":%PORT%" | find "LISTENING" >nul || set "PROBLEM=!PROBLEM! port-%PORT%-not-listening"
netsh advfirewall firewall show rule name="%RULE%" >nul 2>&1 || set "PROBLEM=!PROBLEM! firewall-rule-missing"

if defined PROBLEM (
    echo.
    echo  [X] Finished, but with problems:!PROBLEM!
    echo      The log is at %LOG%
    goto :finished
)

for /f "tokens=2 delims=:" %%A in ('ipconfig ^| find /i "IPv4"') do (
    if not defined IP set "IP=%%A"
)
echo.
echo  [OK] Checked: account, WinRM, port %PORT% and the firewall rule.
echo.
echo  Add this PC on the AnsiWEB PCs page:
echo      PC name:    %COMPUTERNAME%
echo      IP address:%IP%
echo.
echo  The log is at %LOG%
goto :finished

:failed_account
echo  [X] Could not create or update the "%ACCOUNT%" account. See %LOG%
goto :finished
:failed_group
echo  [X] Could not add "%ACCOUNT%" to %ADMINS%. See %LOG%
goto :finished
:failed_registry
echo  [X] Could not set LocalAccountTokenFilterPolicy. See %LOG%
goto :finished
:failed_firewall
echo  [X] Could not add the firewall rule. See %LOG%
goto :finished

:finished
echo.
echo  Press any key to close this window.
pause >nul
endlocal
