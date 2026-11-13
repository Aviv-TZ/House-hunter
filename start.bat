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
title House Hunter - running (keep this window open)
echo Starting the dashboard...
start "House Hunter - dashboard" /min ".venv\Scripts\python.exe" -m streamlit run dashboard.py --server.headless true --browser.gatherUsageStats false
timeout /t 6 >nul
start "" http://localhost:8501
echo.
echo ============================================================
echo   House Hunter is running. Scanning every hour.
echo   Keep this window open (you can minimize it).
echo   Dashboard: http://localhost:8501
echo ============================================================
echo.
".venv\Scripts\python.exe" -m hunter loop
pause
