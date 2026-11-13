@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo [!] Not installed yet. Double-click 1-install.bat first.
  pause
  exit /b 1
)
echo Reading the list of groups you are a member of (a browser window will open, takes ~1 minute)...
".venv\Scripts\python.exe" -m hunter fb-groups
if exist "data\fb_groups.yaml" notepad "data\fb_groups.yaml"
pause
