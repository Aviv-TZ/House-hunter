@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
echo @echo off> "%STARTUP%\house-hunter.bat"
echo start "House Hunter" /min "%~dp0start.bat">> "%STARTUP%\house-hunter.bat"
echo [OK] House Hunter will start automatically when you log in to Windows.
echo      To undo: autostart-off.bat
pause
