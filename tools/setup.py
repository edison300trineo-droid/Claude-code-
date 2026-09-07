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
STEPS = 6


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


BLOCKED_HINTS = (
    "應用程式控制原則",           # 中文 Windows
    "application control policy",  # 英文 Windows
    "blocked by",
)


def verify_imports() -> int:
    """在用到套件之前先確認它們真的載得起來。

    pandas 與 numpy 帶編譯出來的 DLL。企業的應用程式控制原則(WDAC / AppLocker /
    Smart App Control)常會封鎖使用者目錄下未簽章的 DLL，這時 pip 會安裝成功，
    但一 import 就炸。與其讓使用者看到一整串 traceback，不如在這裡講清楚。
    """
    step(3, "確認套件可以載入...")
    probe = (
        "import pandas, openpyxl, xlrd, yaml; "
        "print(pandas.__version__)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT, capture_output=True, text=True,
    )
    if result.returncode == 0:
        say(f"      pandas {result.stdout.strip()} 可正常載入")
        say()
        return 0

    message = (result.stderr or "").strip()
    blocked = any(hint in message.lower() or hint in message
                  for hint in BLOCKED_HINTS)

    if blocked:
        say()
        say("  [錯誤] 套件裝好了，但作業系統不讓它載入。")
        say()
        say("  你的電腦有「應用程式控制原則」(Application Control)，")
        say("  它封鎖了 numpy／pandas 附帶的編譯檔(.pyd/.dll)。")
        say("  這是資訊安全政策，不是本專案的問題 —— 任何 Python 科學運算")
        say("  套件在這台機器上都會遇到同樣的狀況。")
        say()
        say("  可以嘗試的方向（由快到慢）：")
        say("    1. 檢查是否為 Smart App Control：設定 → 隱私權與安全性 →")
        say("       Windows 安全性 → 應用程式與瀏覽器控制 → 智慧型應用程式控制")
        say("    2. 把 Python 裝到 C:\\Program Files 之下（需要系統管理員），")
        say("       有些原則只允許非使用者可寫入的路徑")
        say("    3. 請 IT 將本專案資料夾與 Python 安裝路徑加入允許清單")
        say()
        say("  原始訊息：")
        for line in message.splitlines()[-3:]:
            say(f"    {line}")
        say()
        return 1

    return fail(
        "套件無法載入。",
        "原始訊息：",
        *[f"  {line}" for line in message.splitlines()[-5:]],
    )


def make_folders() -> int:
    step(4, "建立資料夾...")
    for relative in ("data/raw", "data/reference", "output"):
        (ROOT / relative).mkdir(parents=True, exist_ok=True)
    say("      data\\raw、data\\reference、output 已就緒")
    say()
    return 0


def make_decisions() -> int:
    step(5, "準備人工決策表...")
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
    step(6, "執行自我測試，確認安裝正確...")
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

    for stage in (check_python, install_package, verify_imports,
                  make_folders, make_decisions, self_test):
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
