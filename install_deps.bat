@echo off
chcp 65001 >nul
REM 安装托盘版所需的 Python 依赖
setlocal
pushd "%~dp0"
echo 正在安装依赖: pystray Pillow ...
python -m pip install -r requirements.txt
popd
echo.
echo 安装完成。接下来可以双击 run_tray_as_admin.bat 启动托盘。
pause
endlocal
