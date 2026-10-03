@echo off
chcp 65001 >nul
rem Double-clickable launcher for Friskoli-CAD (see start.ps1 for options).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
