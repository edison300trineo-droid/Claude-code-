@echo off
chcp 65001 >nul
setlocal

rem qPCR biodistribution consolidation - produce the summary workbook.
rem Pure ASCII on purpose; see the note in setup.bat. Chinese output comes
rem from the Python CLI.

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

if not exist "config\BD-TS-20260701.yaml" (
    echo.
    echo   [ERROR] config\BD-TS-20260701.yaml not found.
    echo   This file must sit next to the config folder.
    echo.
    pause
    exit /b 1
)

%PY% -m qpcr_biod.cli run -c "config\BD-TS-20260701.yaml"
set EXITCODE=%errorlevel%

echo.
if %EXITCODE% neq 0 (
    echo   [FAILED] Screenshot the whole window and send it for support.
) else (
    echo   [DONE] Summary workbook updated - see the output folder.
)
echo.
pause
exit /b %EXITCODE%
