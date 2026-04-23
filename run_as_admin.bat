@echo off
chcp 65001 >nul
REM 以管理员权限运行 keyboard_lock.py
REM 用法: 双击本文件，确认 UAC 弹窗即可

setlocal
set SCRIPT_DIR=%~dp0

REM 检查当前是否已是管理员
net session >nul 2>&1
if %errorLevel% == 0 (
    echo [INFO] 已是管理员权限，直接运行...
    pushd "%SCRIPT_DIR%"
    python keyboard_lock.py %*
    popd
    pause
) else (
    echo [INFO] 请求管理员权限...
    powershell -Command "Start-Process cmd -ArgumentList '/c chcp 65001 >nul && cd /d %SCRIPT_DIR% && python keyboard_lock.py %* && pause' -Verb RunAs"
)

endlocal
