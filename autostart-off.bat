@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\house-hunter.bat" >nul 2>&1
echo [OK] Automatic start removed.
pause
