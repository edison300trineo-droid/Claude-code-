@echo off
chcp 65001 >nul
setlocal

rem qPCR biodistribution consolidation - first-time setup.
rem
rem This file is deliberately pure ASCII. cmd.exe resumes reading a batch file
rem at a byte offset computed under the previous code page, so a chcp inside a
rem file that contains non-ASCII text slices the following lines apart and the
rem fragments get executed as commands. All Chinese output lives in
rem tools\setup.py instead.

cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY (
    echo.
    echo   [ERROR] Python not found.
    echo   Install Python 3.10 or newer from https://www.python.org/downloads/
    echo   and be sure to tick "Add Python to PATH", then run this file again.
    echo.
    pause
    exit /b 1
)

if not exist "tools\setup.py" (
    echo.
    echo   [ERROR] tools\setup.py not found.
    echo   Extract the whole project folder and run this file from inside it.
    echo.
    pause
    exit /b 1
)

%PY% "tools\setup.py"
set EXITCODE=%errorlevel%

echo.
pause
exit /b %EXITCODE%
