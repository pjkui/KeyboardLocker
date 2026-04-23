@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Requesting administrator privileges...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo [INFO] Running KeyboardLocker tray app as administrator...
start "" /D "%SCRIPT_DIR%" pythonw "%SCRIPT_DIR%tray_app.py"

endlocal
exit /b 0
