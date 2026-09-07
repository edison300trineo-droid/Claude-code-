@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem qPCR 生物分布統整 - 首次安裝
rem 這個只需要跑一次。之後日常使用請執行「執行統整.bat」。

cd /d "%~dp0"

echo ============================================================
echo   qPCR 生物分布統整 - 安裝
echo ============================================================
echo.

rem --- 1. 檢查 Python -----------------------------------------
echo [1/5] 檢查 Python...
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY (
    echo.
    echo   [錯誤] 找不到 Python。
    echo.
    echo   請到 https://www.python.org/downloads/ 下載安裝 Python 3.10 以上版本。
    echo   安裝時務必勾選 "Add Python to PATH"，裝完後重新執行本檔案。
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('%PY% -V 2^>^&1') do set PYVER=%%v
echo       找到 Python !PYVER!（指令：%PY%）
echo.

rem --- 2. 安裝套件 --------------------------------------------
echo [2/5] 安裝程式與相依套件（第一次會花幾分鐘）...
%PY% -m pip install --upgrade pip --quiet
%PY% -m pip install -e ".[dev]" --quiet
if errorlevel 1 (
    echo.
    echo   [錯誤] 套件安裝失敗。
    echo   如果是公司網路擋住 PyPI，請聯繫 IT 或改用離線安裝。
    echo.
    pause
    exit /b 1
)
echo       安裝完成
echo.

rem --- 3. 建立資料夾 ------------------------------------------
echo [3/5] 建立資料夾...
if not exist "data\raw"       mkdir "data\raw"
if not exist "data\reference" mkdir "data\reference"
if not exist "output"         mkdir "output"
echo       data\raw、data\reference、output 已就緒
echo.

rem --- 4. 產生決策表 ------------------------------------------
echo [4/5] 準備人工決策表...
%PY% -m qpcr_biod.cli init-decisions -c "config\BD-TS-20260701.yaml"
echo.

rem --- 5. 自我測試 --------------------------------------------
echo [5/5] 執行自我測試，確認安裝正確...
%PY% -m pytest -q
if errorlevel 1 (
    echo.
    echo   [警告] 自我測試沒有全部通過。
    echo   請把上面的訊息截圖給負責維護的同仁，先不要用在正式資料上。
    echo.
    pause
    exit /b 1
)
echo.

echo ============================================================
echo   安裝完成
echo ============================================================
echo.
echo   接下來：
echo     1. 把原始 qPCR export (.xls) 放進  data\raw\
echo     2. 把動物分組/性別參考檔放進       data\reference\
echo     3. 執行「執行統整.bat」產生統整表
echo.
echo   如果要先跟既有的統整表對帳，請執行「對照驗收.bat」。
echo.
pause
