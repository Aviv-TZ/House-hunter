@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul
echo Opening your settings file in Notepad.
echo Paste the keys after the = sign, then File ^> Save, and close Notepad.
notepad ".env"
if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -m hunter check
pause
