@echo off
chcp 65001 >nul
setlocal

rem qPCR 生物分布統整 - 一鍵執行
rem 雙擊即可。跑完會停在畫面上讓你看結果與警告。

cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY (
    echo [錯誤] 找不到 Python。請先安裝 Python 3.10 以上版本，
    echo        安裝時記得勾選 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

if not exist "config\BD-TS-20260701.yaml" (
    echo [錯誤] 找不到設定檔 config\BD-TS-20260701.yaml
    echo        請確認這個批次檔和 config 資料夾在同一層。
    echo.
    pause
    exit /b 1
)

echo 正在統整 qPCR 資料，請稍候...
echo.

%PY% -m qpcr_biod.cli run -c "config\BD-TS-20260701.yaml"
set EXITCODE=%errorlevel%

echo.
if %EXITCODE% neq 0 (
    echo [執行失敗] 請把上面的訊息整段截圖給負責維護的同仁。
) else (
    echo [完成] 統整表已更新，請開啟 output 資料夾查看。
)
echo.
pause
exit /b %EXITCODE%
