@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo Opening the search criteria (config.yaml) in Notepad. Save with File ^> Save.
notepad "config.yaml"
