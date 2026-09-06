"""動物參考資料（性別、採樣時間點）載入與跨來源交叉驗證。

性別與時間點只從參考檔取得，不由動物編號前綴推測 —— 前綴規則在本研究曾被
證實不足以區分組別，猜測會讓錯誤悄悄進入報告。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .config import StudyConfig


@dataclass
class AnimalRecord:
    animal_id: str
    sex: str = ""
    timepoint: str = ""
    sources: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


@dataclass
class ReferenceData:
    animals: dict[str, AnimalRecord] = field(default_factory=dict)
    files: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def sex_of(self, animal_id: str) -> str:
        record = self.animals.get(animal_id)
        return record.sex if record else ""

    def timepoint_of(self, animal_id: str) -> str:
        record = self.animals.get(animal_id)
        return record.timepoint if record else ""

    def conflicts_of(self, animal_id: str) -> list[str]:
        record = self.animals.get(animal_id)
        return record.conflicts if record else []


def _match_column(columns: list[str], aliases: list[str]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in columns}
    for alias in aliases:
        hit = lowered.get(alias.strip().lower())
        if hit is not None:
            return hit
    return None


def _norm_animal(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if text.isdigit() and len(text) < 4:
        return text.zfill(4)
    return text


def _norm_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def load_reference(reference_dir: str | Path, config: StudyConfig) -> ReferenceData:
    """讀取參考資料夾中所有 xlsx/xls/csv，交叉整併動物性別與時間點。"""
    data = ReferenceData()
    directory = Path(reference_dir)
    if not directory.is_dir():
        data.warnings.append(
            f"找不到參考資料夾 {directory}，本次統整表的性別/採樣時間點欄位將留白。"
        )
        return data

    aliases = config.raw.get("reference", {}).get("column_aliases", {})
    on_conflict = str(config.raw.get("reference", {}).get("on_conflict", "conflict"))

    candidates = sorted(
        p for pattern in ("*.xlsx", "*.xls", "*.csv")
        for p in directory.glob(pattern)
        if not p.name.startswith("~$")
    )
    if not candidates:
        data.warnings.append(
            f"參考資料夾 {directory} 內沒有可讀的檔案，性別/採樣時間點欄位將留白。"
        )
        return data

    for path in candidates:
        rows_used = _ingest_file(path, aliases, on_conflict, data)
        data.files.append({
            "檔案名稱": path.name,
            "檔案類型": "動物分組/性別參考資料",
            "資料筆數": rows_used,
            "狀態": "已匯入" if rows_used else "已讀取但未取得可用欄位",
        })

    return data


def _ingest_file(path: Path, aliases: dict[str, list[str]], on_conflict: str,
                 data: ReferenceData) -> int:
    try:
        if path.suffix.lower() == ".csv":
            frames = {path.stem: pd.read_csv(path, dtype=object)}
        else:
            frames = pd.read_excel(path, sheet_name=None, dtype=object)
    except Exception as exc:  # noqa: BLE001 - 參考檔格式多變，讀不到就跳過並說清楚
        data.warnings.append(f"參考檔 {path.name} 無法讀取（{exc}），已略過。")
        return 0

    used = 0
    for sheet_name, frame in frames.items():
        if frame is None or frame.empty:
            continue
        columns = list(frame.columns)
        animal_col = _match_column(columns, aliases.get("animal_id", []))
        if animal_col is None:
            continue
        sex_col = _match_column(columns, aliases.get("sex", []))
        tp_col = _match_column(columns, aliases.get("timepoint", []))
        if sex_col is None and tp_col is None:
            continue

        origin = f"{path.name}" + (f"[{sheet_name}]" if len(frames) > 1 else "")
        for _, row in frame.iterrows():
            animal_id = _norm_animal(row.get(animal_col))
            if not animal_id:
                continue
            sex = _norm_text(row.get(sex_col)) if sex_col else ""
            timepoint = _norm_text(row.get(tp_col)) if tp_col else ""
            if not sex and not timepoint:
                continue
            _merge_animal(data, animal_id, sex, timepoint, origin, on_conflict)
            used += 1

    return used


def _merge_animal(data: ReferenceData, animal_id: str, sex: str, timepoint: str,
                  origin: str, on_conflict: str) -> None:
    record = data.animals.get(animal_id)
    if record is None:
        record = AnimalRecord(animal_id=animal_id)
        data.animals[animal_id] = record

    if origin not in record.sources:
        record.sources.append(origin)

    for field_name, incoming, label in (("sex", sex, "性別"), ("timepoint", timepoint, "採樣時間點")):
        if not incoming:
            continue
        existing = getattr(record, field_name)
        if not existing:
            setattr(record, field_name, incoming)
            continue
        if existing == incoming:
            continue
        message = (
            f"動物 {animal_id} 的{label}在不同參考檔不一致："
            f"{existing!r} vs {incoming!r}（來源：{'、'.join(record.sources)}）"
        )
        if message not in record.conflicts:
            record.conflicts.append(message)
            data.warnings.append(message)
        if on_conflict == "conflict":
            # 有衝突就留白，逼出人工確認，不擅自選一個
            setattr(record, field_name, "")
