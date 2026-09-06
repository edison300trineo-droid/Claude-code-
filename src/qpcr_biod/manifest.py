"""執行紀錄 (manifest)：讓每一份產出都可以被重現與稽核。

原本的活頁簿靠 Excel 公式連回 Raw_Import 來達成追溯。改成 pipeline 之後，
追溯改由這裡負責：記下每個輸入檔的 SHA-256、程式版本、設定檔、套用了哪些
人工決策、以及所有警告。同樣的輸入 + 同樣的設定 + 同樣的版本 = 同樣的產出。
"""

from __future__ import annotations

import getpass
import platform
import socket
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import __version__


@dataclass
class Manifest:
    study_id: str
    config_path: str
    generated_at: str
    generated_by: str
    machine: str
    tool_version: str
    inputs: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_frame(self) -> pd.DataFrame:
        rows = [
            ("研究編號", self.study_id),
            ("設定檔", self.config_path),
            ("產生時間", self.generated_at),
            ("執行者", self.generated_by),
            ("執行機器", self.machine),
            ("程式版本", self.tool_version),
            ("輸入檔案數", str(len(self.inputs))),
            ("套用之人工決策筆數", str(len(self.decisions))),
            ("警告數", str(len(self.warnings))),
        ]
        return pd.DataFrame(rows, columns=["項目", "內容"])

    def inputs_frame(self) -> pd.DataFrame:
        if not self.inputs:
            return pd.DataFrame(columns=["檔案名稱", "檔案類型", "SHA-256", "狀態"])
        return pd.DataFrame(self.inputs)

    def decisions_frame(self) -> pd.DataFrame:
        if not self.decisions:
            return pd.DataFrame(columns=["決策類型", "對象", "內容", "覆核者", "覆核日期", "理由"])
        return pd.DataFrame(self.decisions)

    def warnings_frame(self) -> pd.DataFrame:
        if not self.warnings:
            return pd.DataFrame([{"序號": 1, "訊息": "本次執行沒有警告。"}])
        return pd.DataFrame(
            [{"序號": i, "訊息": w} for i, w in enumerate(self.warnings, start=1)]
        )


def build_manifest(study_id: str, config_path: Path) -> Manifest:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - 某些排程環境取不到使用者名稱
        user = "(unknown)"
    return Manifest(
        study_id=study_id,
        config_path=str(config_path),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        generated_by=user,
        machine=f"{socket.gethostname()} / {platform.system()} {platform.release()}",
        tool_version=__version__,
    )
