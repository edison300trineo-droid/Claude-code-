@echo off
chcp 65001 >nul
setlocal

rem qPCR biodistribution consolidation - reconcile against an existing workbook.
rem Pure ASCII on purpose; see the note in setup.bat. The prompt and all
rem Chinese output come from tools\reconcile.py.

rem pushd (not cd /d) so the folder can live on a UNC share.
pushd "%~dp0" || (echo   [ERROR] Cannot reach "%~dp0" & pause & exit /b 1)
set PYTHONIOENCODING=utf-8

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY (
    echo.
    echo   [ERROR] Python not found. Run the setup batch file first.
    echo.
    pause
    popd
    exit /b 1
)

%PY% "tools\reconcile.py"
set EXITCODE=%errorlevel%

echo.
pause
popd
exit /b %EXITCODE%
