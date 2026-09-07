"""首次安裝：安裝套件、建立資料夾、產生決策表、跑自我測試。

中文訊息一律由這裡輸出，不寫在 .bat 裡 —— cmd.exe 在切換編碼(chcp)後會用錯誤的
位元組偏移繼續讀取批次檔，把含中文的後續行切碎，切出來的碎片會被當成指令執行。
把 .bat 保持在純 ASCII 就完全避開這個問題。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "BD-TS-20260701.yaml"
MIN_PYTHON = (3, 10)
STEPS = 5


def _setup_console() -> None:
    """Windows 主控台預設不是 UTF-8，中文會變亂碼或直接拋錯。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def say(message: str = "") -> None:
    print(message, flush=True)


def step(index: int, title: str) -> None:
    say(f"[{index}/{STEPS}] {title}")


def fail(title: str, *lines: str) -> int:
    say()
    say(f"  [錯誤] {title}")
    for line in lines:
        say(f"  {line}")
    say()
    return 1


def run(*args: str) -> int:
    return subprocess.call([sys.executable, *args], cwd=ROOT)


def check_python() -> int:
    step(1, "檢查 Python 版本...")
    version = sys.version_info
    if version < MIN_PYTHON:
        return fail(
            f"Python 版本過舊：目前 {version.major}.{version.minor}，需要 3.10 以上。",
            "請到 https://www.python.org/downloads/ 安裝新版，",
            '安裝時務必勾選 "Add Python to PATH"。',
        )
    say(f"      Python {version.major}.{version.minor}.{version.micro}")
    say()
    return 0


def install_package() -> int:
    step(2, "安裝程式與相依套件（第一次會花幾分鐘）...")
    run("-m", "pip", "install", "--upgrade", "pip", "--quiet")
    if run("-m", "pip", "install", "-e", ".[dev]", "--quiet") != 0:
        return fail(
            "套件安裝失敗。",
            "如果是公司網路擋住 PyPI，請聯繫 IT 或改用離線安裝。",
        )
    say("      安裝完成")
    say()
    return 0


def make_folders() -> int:
    step(3, "建立資料夾...")
    for relative in ("data/raw", "data/reference", "output"):
        (ROOT / relative).mkdir(parents=True, exist_ok=True)
    say("      data\\raw、data\\reference、output 已就緒")
    say()
    return 0


def make_decisions() -> int:
    step(4, "準備人工決策表...")
    if not CONFIG.is_file():
        return fail(
            f"找不到設定檔 {CONFIG.relative_to(ROOT)}",
            "請確認解壓後的資料夾結構完整（應該看得到 config、src、tools 等資料夾）。",
        )
    if run("-m", "qpcr_biod.cli", "init-decisions", "-c", str(CONFIG)) != 0:
        return fail("決策表產生失敗。")
    say()
    return 0


def self_test() -> int:
    step(5, "執行自我測試，確認安裝正確...")
    if run("-m", "pytest", "-q") != 0:
        say()
        say("  [警告] 自我測試沒有全部通過。")
        say("  請把上面的訊息截圖給負責維護的同仁，先不要用在正式資料上。")
        say()
        return 1
    say()
    return 0


def main() -> int:
    _setup_console()
    say("=" * 60)
    say("  qPCR 生物分布統整 - 安裝")
    say("=" * 60)
    say()

    for stage in (check_python, install_package, make_folders,
                  make_decisions, self_test):
        code = stage()
        if code != 0:
            return code

    say("=" * 60)
    say("  安裝完成")
    say("=" * 60)
    say()
    say("  接下來：")
    say("    1. 把原始 qPCR export (.xls) 放進  data\\raw\\")
    say("    2. 把動物分組/性別參考檔放進       data\\reference\\")
    say("    3. 執行「執行統整.bat」產生統整表")
    say()
    say("  如果要先跟既有的統整表對帳，請執行「對照驗收.bat」。")
    say()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
