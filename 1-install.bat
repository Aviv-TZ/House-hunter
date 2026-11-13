@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
title House Hunter - install
echo ============================================
echo   House Hunter - installation (5-10 minutes)
echo ============================================
echo.
py -3 --version >nul 2>&1
if errorlevel 1 (
  echo [!] Python is not installed ^(or "Add python.exe to PATH" was not ticked^).
  echo     Opening python.org - install Python, then double-click this file again.
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating a private Python environment...
  py -3 -m venv .venv
  if errorlevel 1 ( echo [!] Failed to create the environment. & pause & exit /b 1 )
)
echo [2/4] Installing packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [!] Package install failed - check your internet connection and try again. & pause & exit /b 1 )
echo [3/4] Installing the browser used for Facebook...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 ( echo [!] Browser install failed - try running this file again. & pause & exit /b 1 )
echo [4/4] Checking...
".venv\Scripts\python.exe" -m pytest -q tests
if not exist ".env" copy ".env.example" ".env" >nul
echo.
echo ============================================
echo   Done! Next: 2-settings.bat
echo ============================================
pause
