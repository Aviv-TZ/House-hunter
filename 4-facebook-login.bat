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
echo A browser window will open. Log in to Facebook there as usual.
echo When you see your Facebook feed, come back to THIS window and press Enter.
".venv\Scripts\python.exe" -m hunter fb-login
pause
