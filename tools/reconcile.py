"""對照驗收：把 pipeline 產出與既有統整表逐列對帳。

接受拖曳進來的路徑（會自動去掉引號）。中文訊息在這裡輸出，理由同 setup.py。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "BD-TS-20260701.yaml"


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def ask_for_workbook() -> Path | None:
    print("=" * 60, flush=True)
    print("  對照驗收 - pipeline 產出 vs. 既有統整表", flush=True)
    print("=" * 60, flush=True)
    print(flush=True)
    print("請把既有的統整表 .xlsx 拖曳到這個視窗後按 Enter", flush=True)
    print("（或直接貼上完整路徑；按 Enter 不輸入則取消）", flush=True)
    print(flush=True)

    try:
        raw = input("既有統整表路徑: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    # 拖曳進 cmd 視窗的路徑常帶引號
    raw = raw.strip('"').strip("'").strip()
    if not raw:
        return None
    return Path(raw)


def main() -> int:
    _setup_console()
    target = ask_for_workbook()
    if target is None:
        print("\n已取消。", flush=True)
        return 0
    if not target.is_file():
        print(f"\n[錯誤] 找不到檔案：{target}", flush=True)
        return 1

    print("\n正在對帳，請稍候...\n", flush=True)
    code = subprocess.call(
        [sys.executable, "-m", "qpcr_biod.cli", "compare",
         "-c", str(CONFIG), "--against", str(target)],
        cwd=ROOT,
    )

    print(flush=True)
    if code == 0:
        print("[結果] 對帳完成，詳見上方摘要與 output 資料夾的對照報告。", flush=True)
    else:
        print("[結果] 有值不一致，完整清單請看 output 資料夾裡的對照報告。", flush=True)
        print("       差異不一定是 pipeline 錯 —— 舊表若有未記錄的人工調整也會出現在這裡。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
