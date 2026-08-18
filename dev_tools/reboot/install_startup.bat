@echo off
chcp 65001 > nul

REM Check admin privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting admin privileges...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"%~f0\"' -Verb RunAs"
    exit /b
)

echo Installing OAS Daemon to Windows Startup (with highest privileges)...

set SCRIPT_DIR=%~dp0
set DAEMON_PY=%SCRIPT_DIR%reboot_daemon.py
set PYTHONW=

REM 优先使用项目自带 Python，避免系统 PATH 中没有 pythonw
if exist "%SCRIPT_DIR%..\..\toolkit\pythonw.exe" (
    set PYTHONW=%SCRIPT_DIR%..\..\toolkit\pythonw.exe
) else (
    for /f "delims=" %%i in ('where pythonw 2^>nul') do set PYTHONW=%%i
)

if not defined PYTHONW (
    echo [ERROR] pythonw.exe not found.
    echo Please check the toolkit directory or install Python.
    pause
    exit /b 1
)
if not exist "%PYTHONW%" (
    echo [ERROR] pythonw.exe not found: %PYTHONW%
    pause
    exit /b 1
)

echo Daemon: %DAEMON_PY%
echo Pythonw: %PYTHONW%

REM Remove old startup methods
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "OASDaemon" /f >nul 2>&1
del "%TEMP%\oas_daemon_start.vbs" >nul 2>&1
schtasks /delete /tn "OASDaemon" /f >nul 2>&1

REM Create scheduled task with highest privileges - directly run pythonw (no cmd window)
schtasks /create /tn "OASDaemon" /tr "\"%PYTHONW%\" \"%DAEMON_PY%\"" /sc ONLOGON /rl HIGHEST /f
if %errorLevel% equ 0 goto :success

REM Fallback: registry startup
echo [FAIL] Scheduled task creation failed, trying registry fallback...
echo Set WshShell = CreateObject("WScript.Shell") > "%TEMP%\oas_daemon_start.vbs"
echo WshShell.Run """%PYTHONW%"" ""%DAEMON_PY%""", 0, False >> "%TEMP%\oas_daemon_start.vbs"
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "OASDaemon" /t REG_SZ /d "wscript ""%TEMP%\oas_daemon_start.vbs""" /f
echo [OK] Installed via registry (fallback, may need manual admin run)
goto :done

:success
echo.
echo [OK] Scheduled task "OASDaemon" created
echo     - Runs as admin on logon, no UAC prompt
echo     - Manual start: schtasks /run /tn "OASDaemon"
echo     - Uninstall: uninstall_startup.bat

:done
echo.
pause
