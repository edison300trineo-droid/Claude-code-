"""臟器批次與上機編號 (Run) 進度表。

官方「上機編號_說明」對照表把臟器分成幾個批次，同批次的臟器在同一個 Run 內
一起上機。但官方表只預先排定了前幾個 Run；後面的 Run 得從實際匯入的資料逆推。

這兩種來源必須分得開 —— 逆推出來的東西不能看起來跟官方排定的一樣可靠。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .classify import SampleClass
from .config import StudyConfig

SOURCE_OFFICIAL = "官方上機編號對照表"
SOURCE_INFERRED = "依實際匯入資料逆推"


@dataclass
class BatchTables:
    composition: pd.DataFrame = field(default_factory=pd.DataFrame)
    progress: pd.DataFrame = field(default_factory=pd.DataFrame)
    notes: list[str] = field(default_factory=list)


def build_batch_tables(wells: pd.DataFrame, consolidated: pd.DataFrame,
                       run_order: dict[str, int],
                       config: StudyConfig) -> BatchTables:
    tables = BatchTables()
    batches = config.raw.get("batches") or {}
    if not batches:
        tables.notes.append("設定檔未定義 batches，未產生批次與上機編號進度表。")
        return tables

    tables.composition = _composition(batches, config)
    tables.progress = _progress(wells, consolidated, run_order, batches, config,
                                tables.notes)
    return tables


def _composition(batches: dict[str, list[str]], config: StudyConfig) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name, codes in batches.items():
        row: dict[str, Any] = {"批次(Batch)": name}
        for index, code in enumerate(codes, start=1):
            code = str(code)
            english = config.organ_name(code, "en")
            chinese = config.organ_name(code, "zh")
            row[f"臟器代碼{index}"] = code
            row[f"臟器名稱{index}"] = (
                f"{chinese} / {english}" if chinese or english else "(尚未收錄)"
            )
        rows.append(row)
    return pd.DataFrame(rows)


def _progress(wells: pd.DataFrame, consolidated: pd.DataFrame,
              run_order: dict[str, int], batches: dict[str, list[str]],
              config: StudyConfig, notes: list[str]) -> pd.DataFrame:
    schedule = config.raw.get("run_schedule") or {}
    official = schedule.get("official") or []
    official_index = {
        (str(entry.get("batch")), str(entry.get("timepoint"))): entry.get("run")
        for entry in official
    }

    animals = wells[wells["sample_class"] == SampleClass.ANIMAL.value]
    timepoints = _timepoints_by_file(consolidated)

    rows: list[dict[str, Any]] = []
    for source_file in sorted(run_order, key=lambda f: run_order[f]):
        codes = sorted({
            str(c) for c in animals.loc[
                animals["source_file"] == source_file, "organ_code"
            ].dropna()
        })
        batch, match_quality = _match_batch(codes, batches)
        file_timepoints = sorted(timepoints.get(source_file, set()))
        timepoint = file_timepoints[0] if len(file_timepoints) == 1 else ""

        official_run = official_index.get((batch, timepoint)) if batch else None
        rows.append({
            "Run編號(推定)": run_order[source_file] + 1,
            "批次(Batch)": batch or "(無法對應)",
            "批次比對": match_quality,
            "採樣時間點": timepoint or ("、".join(file_timepoints) or "(未提供)"),
            "對應原始檔案": source_file,
            "官方已排定Run編號": official_run if official_run is not None else "",
            "批次/時間點資料來源": SOURCE_OFFICIAL if official_run is not None else SOURCE_INFERRED,
            "本檔出現的臟器代碼": "、".join(codes) or "(無動物檢體)",
        })

        if batch is None and codes:
            notes.append(
                f"{source_file} 出現的臟器代碼（{'、'.join(codes)}）"
                "無法對應到設定檔中任何一個批次，請確認 batches 設定或該檔內容。"
            )
        if len(file_timepoints) > 1:
            notes.append(
                f"{source_file} 內含多個採樣時間點（{'、'.join(file_timepoints)}），"
                "無法對應到單一 Run 排程，請人工確認。"
            )

    notes.append(
        "Run 編號為推定值：原始檔案內沒有 Run 編號欄位，本表依 Run 結束時間排序推定，"
        "建議與儀器/實驗紀錄核對。「批次/時間點資料來源」欄標示為"
        f"「{SOURCE_INFERRED}」者，未經官方對照表明文排定，請特別核對。"
    )
    return pd.DataFrame(rows)


def _timepoints_by_file(consolidated: pd.DataFrame) -> dict[str, set[str]]:
    if consolidated.empty:
        return {}
    result: dict[str, set[str]] = {}
    for _, row in consolidated.iterrows():
        timepoint = str(row.get("採樣時間點(Time point)") or "").strip()
        if not timepoint:
            continue
        result.setdefault(str(row["來源檔案"]), set()).add(timepoint)
    return result


def _match_batch(codes: list[str], batches: dict[str, list[str]]) -> tuple[str | None, str]:
    """把一個檔案出現的臟器代碼對應到批次。

    完全相符最理想；只出現部分臟器（例如某些動物該臟器沒送測）仍算相符，
    但會標示為部分，讓人知道這個判定沒有那麼硬。
    """
    if not codes:
        return None, "(無動物檢體)"
    observed = set(codes)
    for name, batch_codes in batches.items():
        expected = {str(c) for c in batch_codes}
        if observed == expected:
            return name, "完全相符"
    for name, batch_codes in batches.items():
        expected = {str(c) for c in batch_codes}
        if observed and observed.issubset(expected):
            missing = sorted(expected - observed)
            return name, f"部分相符（本檔未出現：{'、'.join(missing)}）"
    return None, "無法對應"
