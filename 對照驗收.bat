@echo off
chcp 65001 >nul
setlocal

rem 把 pipeline 產出與既有統整表逐列對帳。
rem 導入期用來確認新流程能重現你已經確認過的結果。

cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY (
    echo [錯誤] 找不到 Python，請先執行「安裝.bat」。
    echo.
    pause
    exit /b 1
)

echo ============================================================
echo   對照驗收 - pipeline 產出 vs. 既有統整表
echo ============================================================
echo.
echo 請把既有的統整表 .xlsx 拖曳到這個視窗後按 Enter
echo （或直接貼上完整路徑）
echo.
set /p OLDFILE=既有統整表路徑:

rem 去掉拖曳時可能帶上的引號
set OLDFILE=%OLDFILE:"=%

if not exist "%OLDFILE%" (
    echo.
    echo [錯誤] 找不到檔案：%OLDFILE%
    echo.
    pause
    exit /b 1
)

echo.
echo 正在對帳，請稍候...
echo.

%PY% -m qpcr_biod.cli compare -c "config\BD-TS-20260701.yaml" --against "%OLDFILE%"
set EXITCODE=%errorlevel%

echo.
if %EXITCODE% equ 0 (
    echo [結果] 完全一致。可以放心切換到新流程。
) else (
    echo [結果] 有差異，詳細清單請看 output 資料夾裡的對照報告。
    echo        差異不一定是 pipeline 錯 - 舊表若有未記錄的人工調整也會出現在這裡。
)
echo.
pause
exit /b 0
