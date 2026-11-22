@echo off
rem WorkBuddy Task Monitor - open the local control panel
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
echo  [ERROR] Python not found.
echo  Please install Python 3.8+ and enable "Add Python to PATH".
echo  Download: https://www.python.org/downloads/
echo.
pause
