@echo off
chcp 65001 >nul
setlocal

rem qPCR biodistribution - show which version of the code is actually installed.
rem After overwriting the folder with a new download, run this first: if the
rem version below is not the one you expect, the update did not land and there
rem is no point re-running the consolidation.
rem Pure ASCII on purpose; see the note in setup.bat.

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

echo.
echo   ---- Installed version ----
%PY% -m qpcr_biod.cli --version
if errorlevel 1 (
    echo.
    echo   [ERROR] qpcr-biod is not installed. Run the setup batch file first.
    echo.
    pause
    popd
    exit /b 1
)

echo.
echo   ---- Code folder actually being used ----
%PY% -c "import qpcr_biod, os; print(os.path.dirname(qpcr_biod.__file__))"

echo.
echo   ---- Date of the file that draws the summary layout ----
dir /T:W "src\qpcr_biod\report.py" | findstr "report.py"

echo.
echo   If the version is not the one you expect, the download did not overwrite
echo   this folder. Delete this folder, extract the new ZIP again, and re-run
echo   the setup batch file.
echo.
pause
popd
exit /b 0
