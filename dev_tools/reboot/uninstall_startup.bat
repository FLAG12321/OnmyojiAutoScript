@echo off
chcp 65001 > nul
echo Uninstalling OAS Daemon from Windows Startup...

REM Remove startup entry from Windows registry
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "OASDaemon" /f

REM Check if temporary startup script exists and delete it
if exist "%TEMP%\oas_daemon_start.vbs" (
    del "%TEMP%\oas_daemon_start.vbs"
    echo Temporary startup script removed from TEMP directory.
) else (
    echo Temporary startup script not found in TEMP directory.
)

echo.
echo OAS Daemon has been uninstalled from Windows startup.
echo.
pause