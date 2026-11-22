@echo off
chcp 65001 >nul
cd /d "%~dp0"

where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe "monitor_panel.py"
    exit /b
)

where pyw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pyw.exe "monitor_panel.py"
    exit /b
)

where python.exe >nul 2>nul
if %errorlevel%==0 (
    start "" python.exe "monitor_panel.py"
    exit /b
)

echo.
echo  [错误] 没有找到 Python。
echo  请先安装 Python 3.8 或更高版本，安装时勾选 "Add Python to PATH"。
echo  下载地址： https://www.python.org/downloads/
echo.
pause
