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
                       run_order: dict[str, int], config: StudyConfig,
                       reference=None) -> BatchTables:
    """官方對照表若提供了批次與排程就以它為準，設定檔只是沒有官方檔時的備援。"""
    tables = BatchTables()

    official_batches = dict(getattr(reference, "batches", {}) or {})
    official_schedule = list(getattr(reference, "run_schedule", []) or [])

    if official_batches:
        batches = official_batches
        tables.notes.append(
            "批次組成取自官方上機編號對照表"
            f"（{'、'.join(getattr(reference, 'official_files', []) or ['官方檔'])}）。"
        )
    else:
        batches = config.raw.get("batches") or {}
        if batches:
            tables.notes.append(
                "參考資料夾中沒有官方上機編號對照表，批次組成改用設定檔的定義，請核對。"
            )

    if not batches:
        tables.notes.append("設定檔與官方對照表都沒有批次定義，未產生批次與上機編號進度表。")
        return tables

    schedule = official_schedule or (config.raw.get("run_schedule") or {}).get("official") or []
    alternatives = _alternatives(config)
    tables.composition = _composition(batches, config, reference, alternatives)
    tables.progress = _progress(wells, consolidated, run_order, batches, schedule,
                                config, alternatives, tables.notes)
    return tables


def _alternatives(config: StudyConfig) -> dict[str, list[str]]:
    """{主要代碼: [可互換的代碼, ...]}，例如睪丸(25)與卵巢(11)共用同一個位置。"""
    raw = config.raw.get("organ_code_alternatives") or {}
    return {str(k): [str(v) for v in values] for k, values in raw.items()}


def _composition(batches: dict[str, list[str]], config: StudyConfig,
                 reference=None, alternatives: dict[str, list[str]] | None = None
                 ) -> pd.DataFrame:
    alternatives = alternatives or {}
    official_organs = dict(getattr(reference, "organ_codes", {}) or {})
    rows: list[dict[str, Any]] = []
    for name, codes in batches.items():
        row: dict[str, Any] = {"批次(Batch)": name}
        for index, code in enumerate(codes, start=1):
            code = str(code)
            names = official_organs.get(code)
            english = names.get("en") if names else config.organ_name(code, "en")
            chinese = names.get("zh") if names else config.organ_name(code, "zh")
            label = f"{chinese} / {english}" if chinese or english else "(尚未收錄)"
            swaps = alternatives.get(code, [])
            if swaps:
                spelled = "、".join(
                    f"{alt} {config.organ_name(alt, 'zh') or alt}" for alt in swaps
                )
                row[f"臟器代碼{index}"] = f"{code}（或 {'、'.join(swaps)}）"
                label += f"；此位置亦可為 {spelled}"
            else:
                row[f"臟器代碼{index}"] = code
            row[f"臟器名稱{index}"] = label
        rows.append(row)
    return pd.DataFrame(rows)


def _progress(wells: pd.DataFrame, consolidated: pd.DataFrame,
              run_order: dict[str, int], batches: dict[str, list[str]],
              schedule: list[dict[str, Any]], config: StudyConfig,
              alternatives: dict[str, list[str]], notes: list[str]) -> pd.DataFrame:
    official_index = {
        (str(entry.get("batch")), config.normalize_timepoint(entry.get("timepoint"))):
            entry.get("run")
        for entry in schedule
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
        batch, match_quality = _match_batch(codes, batches, alternatives)
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

        # 實務上同一盤常會補做計畫外的臟器，所以跨批次與多時間點都是常態。
        # 這些只是「此 Run 不對應官方排程的單一格子」的說明，不是待辦事項 ——
        # 把常態寫成「請人工確認」，久了會讓人學會忽略所有警告。
        if batch is None and codes:
            notes.append(
                f"{source_file} 的臟器組合（{'、'.join(codes)}）不屬於官方排程的任一批次，"
                "Run 編號改由匯入資料推定。同一盤補做計畫外臟器時屬正常。"
            )
        if len(file_timepoints) > 1:
            notes.append(
                f"{source_file} 內含多個採樣時間點（{'、'.join(file_timepoints)}），"
                "未對應到官方排程的單一時間點。同一盤混合不同時間點時屬正常。"
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


def _match_batch(codes: list[str], batches: dict[str, list[str]],
                 alternatives: dict[str, list[str]]) -> tuple[str | None, str]:
    """把一個檔案出現的臟器代碼對應到批次。

    以「上機位置」為單位判定，不是以代碼。一個位置可能接受多個代碼
    （例如性腺位置：公鼠睪丸 25 / 母鼠卵巢 11），只要其中一個出現就算這個
    位置有做到 —— 說成「未出現 11」會讓人以為漏做了一個臟器，其實沒有。
    """
    if not codes:
        return None, "(無動物檢體)"
    observed = set(codes)

    def slots(batch_codes: list[str]) -> list[tuple[str, set[str]]]:
        """回傳 [(批次寫的主要代碼, 這個位置接受的所有代碼), ...]。"""
        return [
            (str(code), {str(code), *alternatives.get(str(code), [])})
            for code in batch_codes
        ]

    best: tuple[str, list[tuple[str, set[str]]]] | None = None
    for name, batch_codes in batches.items():
        batch_slots = slots(batch_codes)
        accepted = set().union(*(codes for _, codes in batch_slots)) if batch_slots else set()
        if not observed.issubset(accepted):
            continue  # 有這個批次容不下的臟器
        unfilled = [slot for slot in batch_slots if not (slot[1] & observed)]
        if not unfilled:
            return name, "完全相符"
        if best is None or len(unfilled) < len(best[1]):
            best = (name, unfilled)

    if best is None:
        return None, "無法對應"

    name, unfilled = best
    # 回報批次自己寫的主要代碼，而不是替代代碼 —— 對照批次組成表才看得懂
    missing = "、".join(primary for primary, _ in unfilled)
    return name, f"部分相符（本檔未出現：{missing}）"
