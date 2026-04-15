@echo off
chcp 65001 > nul
echo Installing OAS Daemon to Windows Startup...

REM Get the directory where this script is located
set SCRIPT_DIR=%~dp0

REM Get the project root directory (two levels up from dev_tools\reboot\)
for %%I in ("%SCRIPT_DIR%..\..") do set PROJECT_ROOT=%%~dpfI

REM Remove trailing backslash from PROJECT_ROOT
if "%PROJECT_ROOT:~-1%"=="\" set PROJECT_ROOT=%PROJECT_ROOT:~0,-1%

REM Set absolute paths
set DAEMON_SCRIPT=%PROJECT_ROOT%\dev_tools\reboot\reboot_daemon.py
set CONFIG_FILE=%PROJECT_ROOT%\dev_tools\reboot\daemon_config.json

echo Project Root: %PROJECT_ROOT%
echo Daemon Script: %DAEMON_SCRIPT%
echo Config File: %CONFIG_FILE%

REM Create VBS script to run daemon in background (no console window)
echo Set WshShell = CreateObject("WScript.Shell") > "%TEMP%\oas_daemon_start.vbs"
echo WshShell.Run "cmd /c cd /d ""%PROJECT_ROOT%"" && pythonw ""%DAEMON_SCRIPT%""", 0, False >> "%TEMP%\oas_daemon_start.vbs"

REM Add VBS script to Windows registry
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "OASDaemon" /t REG_SZ /d "wscript ""%TEMP%\oas_daemon_start.vbs""" /f

echo.
echo Startup script created at: %TEMP%\oas_daemon_start.vbs
echo.
echo OAS Daemon installed to Windows startup successfully.
echo.
echo To uninstall startup, run uninstall_startup.bat
echo.
pause
