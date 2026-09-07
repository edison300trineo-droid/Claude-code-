@echo off
chcp 65001 >nul
setlocal

rem qPCR biodistribution consolidation - reconcile against an existing workbook.
rem Pure ASCII on purpose; see the note in setup.bat. The prompt and all
rem Chinese output come from tools\reconcile.py.

cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY (
    echo.
    echo   [ERROR] Python not found. Run the setup batch file first.
    echo.
    pause
    exit /b 1
)

%PY% "tools\reconcile.py"
set EXITCODE=%errorlevel%

echo.
pause
exit /b %EXITCODE%
