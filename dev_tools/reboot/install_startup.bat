@echo off
chcp 65001 > nul
echo Installing OAS Daemon to Windows Startup...

REM Get the directory where this script is located
set SCRIPT_DIR=%~dp0

REM Get the project root directory
for %%I in ("%SCRIPT_DIR%\..\..") do set PROJECT_ROOT=%%~dpfI

REM Create startup script in TEMP directory
echo @echo off > "%TEMP%\oas_daemon_start.bat"
echo cd /d "%PROJECT_ROOT%" >> "%TEMP%\oas_daemon_start.bat"
echo python dev_tools\reboot\reboot_daemon.py --config-file dev_tools\reboot\daemon_config.json >> "%TEMP%\oas_daemon_start.bat"

REM Add startup script to Windows registry
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "OASDaemon" /t REG_SZ /d "%TEMP%\oas_daemon_start.bat" /f

echo.
echo OAS Daemon installed to Windows startup successfully.
echo.
echo To uninstall startup, run uninstall_startup.bat
echo.
pause