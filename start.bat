@echo off
chcp 65001 >nul
title 案件追蹤系統 - Trifecta MedTek
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo 找不到 Python，請先至 python.org 安裝 Python 3，安裝時勾選 "Add Python to PATH"。
  pause
  exit /b 1
)

echo 正在啟動案件追蹤系統，稍候會自動開啟瀏覽器。
echo.
echo   ★ 這個黑色視窗請保持開著，關掉服務就停了，同仁會連不進來。
echo   ★ 把畫面上「同仁連線」那一行的網址發給同事即可。
echo.
python run.py --open %*
pause
