@echo off
chcp 65001 >nul
setlocal

rem qPCR biodistribution consolidation - application control policy diagnostic.
rem Pure ASCII on purpose; all Chinese output comes from the PowerShell script.
rem Read-only: this changes no system setting.

cd /d "%~dp0"

if not exist "tools\diagnose.ps1" (
    echo.
    echo   [ERROR] tools\diagnostic script not found.
    echo   Extract the whole project folder and run this file from inside it.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\diagnose.ps1"

echo.
echo   Copy the whole output above and send it back for analysis.
echo.
pause
