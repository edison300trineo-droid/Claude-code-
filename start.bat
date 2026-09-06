@echo off
chcp 65001 >nul
title 案件追蹤台帳 - Trifecta MedTek
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo 找不到 Python，請先至 python.org 安裝 Python 3，安裝時勾選 "Add Python to PATH"。
  pause
  exit /b 1
)

echo 正在啟動案件追蹤台帳，請勿關閉此視窗。
echo 關閉視窗或按 Ctrl+C 即停止服務。
echo.
python run.py %*
pause
