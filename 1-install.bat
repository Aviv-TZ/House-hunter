@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
title House Hunter - install
echo ============================================
echo   House Hunter - installation (5-10 minutes)
echo ============================================
echo.
rem --- find Python 3.10 or newer (older versions can't run the Claude library) ---
set "PYCMD="
for %%v in (3.15 3.14 3.13 3.12 3.11 3.10) do (
  if not defined PYCMD (
    py -%%v -c "import sys" >nul 2>&1 && set "PYCMD=py -%%v"
  )
)
if not defined PYCMD (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PYCMD=python"
)
if not defined PYCMD (
  echo [!] Python 3.10 or newer was not found.
  py -3 --version >nul 2>&1 && (
    echo     You have an OLD Python:
    py -3 --version
    echo     It is too old. Install the NEWEST Python from python.org - it installs next to the old one.
  )
  echo     Remember to tick "Add python.exe to PATH" in the installer.
  echo     Opening python.org - install, then double-click this file again.
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)
echo Using:
%PYCMD% --version
rem --- an environment built with an old Python must be rebuilt ---
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo Removing an environment made with an old Python...
    rmdir /s /q ".venv"
  )
)
if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating a private Python environment...
  %PYCMD% -m venv .venv
  if errorlevel 1 ( echo [!] Failed to create the environment. & pause & exit /b 1 )
)
echo [2/4] Installing packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [!] Package install failed. Send a screenshot of this window. & pause & exit /b 1 )
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
