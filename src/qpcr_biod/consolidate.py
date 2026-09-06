"""建構「統整數據」：每一列 = 一個動物編號＋臟器代碼＋來源檔案。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .classify import SampleClass
from .config import StudyConfig
from .curves import StandardCurve
from .decisions import DecisionSet
from .reference import ReferenceData

# 列的視覺標記，交給 report 決定實際底色
STYLE_NORMAL = "normal"
STYLE_MANUAL_REVIEW = "manual_review"   # HIGHSD 且可定量 -> 需人工複核
STYLE_EXPECTED_ND = "expected_nd"       # HIGHSD 但為 ND -> 屬預期現象
STYLE_NOT_FINAL = "not_final"           # 非最終採用列，保留供追溯


@dataclass
class ConsolidationResult:
    table: pd.DataFrame
    warnings: list[str] = field(default_factory=list)
    manual_review_count: int = 0


def build_consolidated(wells: pd.DataFrame, curves: dict[str, StandardCurve],
                       reference: ReferenceData, decisions: DecisionSet,
                       config: StudyConfig) -> ConsolidationResult:
    """把逐孔資料整併成報告層級的統整表。"""
    warnings: list[str] = []
    animals = wells[wells["sample_class"] == SampleClass.ANIMAL.value]
    if animals.empty:
        return ConsolidationResult(pd.DataFrame(), ["沒有任何動物檢體資料。"], 0)

    nd_label = config.nd_label
    unit = config.unit

    rows: list[dict[str, Any]] = []
    for (animal_id, organ_code, source_file), group in animals.groupby(
        ["animal_id", "organ_code", "source_file"], dropna=True
    ):
        curve = curves.get(source_file)
        lloq = curve.lloq if curve else None
        rows.append(_build_row(
            animal_id, organ_code, source_file, group, lloq,
            reference, decisions, config, unit, nd_label, warnings,
        ))

    table = pd.DataFrame(rows)
    table = _apply_final_use(table, decisions, config, warnings)
    table = _finalise_highsd(table, decisions, config, nd_label, warnings)

    sort_key = table["採樣時間點(Time point)"].map(config.timepoint_sort_key)
    table = table.assign(_tp=sort_key).sort_values(
        ["臟器代碼", "_tp", "動物編號", "來源檔案"]
    ).drop(columns="_tp").reset_index(drop=True)

    manual = int((table["列標記"] == STYLE_MANUAL_REVIEW).sum())
    return ConsolidationResult(table, warnings, manual)


def _build_row(animal_id: str, organ_code: str, source_file: str,
               group: pd.DataFrame, lloq: float | None,
               reference: ReferenceData, decisions: DecisionSet,
               config: StudyConfig, unit: str, nd_label: str,
               warnings: list[str]) -> dict[str, Any]:
    wells_sorted = group.sort_values("well")
    quantity_mean = _instrument_or_computed(group, "quantity_mean", "quantity")
    ct_mean = _instrument_or_computed(group, "ct_mean", "ct")

    highsd_values = {str(v).strip().upper() for v in group["highsd"].dropna()}
    if "Y" in highsd_values:
        highsd = "Y"
    elif highsd_values:
        highsd = "N"
    else:
        highsd = ""  # 整盤皆為 N 時儀器不輸出此欄，留白而非填 N

    sex, timepoint, group_name = _animal_metadata(
        animal_id, reference, decisions, config, warnings
    )

    organ_names = _organ_names(organ_code, decisions, config)

    row: dict[str, Any] = {
        "動物編號": animal_id,
        "臟器代碼": organ_code,
        "臟器名稱": organ_names[0],
        "Sample Name": f"{animal_id}_{organ_code}",
        "版本": _version_of(group),
        "最終採用": "",          # 稍後由 _apply_final_use 決定
        "Quantity Mean 定量平均值": quantity_mean,
        "單位": unit,
        "Ct Mean": ct_mean,
        "HIGHSD": highsd,
        "來源檔案": source_file,
        "Run結束時間": _first(group, "run_end_time"),
        "備註": "",
        "複核狀態": "",
        "LLOQ(該run標準曲線最後一點,pg)": lloq,
        "呈現結果(低於LLOQ標示ND)": "",   # 稍後由 _finalise_highsd 決定
        "原始測得平均值(未覆蓋)": quantity_mean,
        "性別(Sex)": sex,
        "組別(Group)": group_name,
        "採樣時間點(Time point)": timepoint,
        "最終列比對Key(輔助)": f"{animal_id}|{organ_code}",
        "列標記": STYLE_NORMAL,
    }

    # 兩個重複孔位的明細，讓 HIGHSD 需人工複核時看得到原始依據
    for index in range(2):
        label = f"孔位{index + 1}"
        if index < len(wells_sorted):
            well_row = wells_sorted.iloc[index]
            row[f"{label}-Well"] = well_row.get("well")
            row[f"{label}-Ct"] = well_row.get("ct")
            row[f"{label}-Quantity"] = well_row.get("quantity")
        else:
            row[f"{label}-Well"] = None
            row[f"{label}-Ct"] = None
            row[f"{label}-Quantity"] = None

    row["孔位數"] = len(wells_sorted)
    if len(wells_sorted) > 2:
        warnings.append(
            f"{animal_id}_{organ_code} @ {source_file} 有 {len(wells_sorted)} 個孔位，"
            "統整表僅列出前兩孔明細，完整資料請見 Raw_Import。"
        )
    return row


def _instrument_or_computed(group: pd.DataFrame, mean_column: str,
                            raw_column: str) -> float | None:
    """優先採用儀器算好的平均值；儀器沒給才自行由孔位平均。"""
    reported = group[mean_column].dropna()
    if not reported.empty:
        return float(reported.iloc[0])
    values = group[raw_column].dropna()
    if values.empty:
        return None
    return float(values.mean())


def _first(group: pd.DataFrame, column: str):
    values = group[column].dropna()
    return values.iloc[0] if not values.empty else None


def _version_of(group: pd.DataFrame) -> str:
    if "version" not in group.columns:
        return "原始"
    values = group["version"].dropna()
    return str(values.iloc[0]) if not values.empty else "原始"


def _organ_names(organ_code: str, decisions: DecisionSet,
                 config: StudyConfig) -> tuple[str, str]:
    override = decisions.organ_override.get(str(organ_code))
    if override:
        return override.get("en", ""), override.get("zh", "")
    return config.organ_name(organ_code, "en"), config.organ_name(organ_code, "zh")


def _animal_metadata(animal_id: str, reference: ReferenceData,
                     decisions: DecisionSet, config: StudyConfig,
                     warnings: list[str]) -> tuple[str, str, str]:
    sex = reference.sex_of(animal_id)
    timepoint = reference.timepoint_of(animal_id)

    override = decisions.group_override.get(animal_id)
    if override:
        return sex, timepoint, override["組別"]

    if not timepoint:
        return sex, timepoint, ""

    group_name = config.group_for_timepoint(timepoint)
    ambiguous = str(config.raw.get("groups", {}).get("ambiguous_label", "待確認"))
    if group_name == ambiguous:
        message = (
            f"動物 {animal_id} 的採樣時間點「{timepoint}」對應到多個組別，無法自動判定。"
            "請在決策表「動物分組指定」分頁指定組別。"
        )
        if message not in warnings:
            warnings.append(message)
    return sex, timepoint, group_name


def _apply_final_use(table: pd.DataFrame, decisions: DecisionSet,
                     config: StudyConfig, warnings: list[str]) -> pd.DataFrame:
    """決定每個 動物_臟器 的最終採用列。

    預設規則：有 rerun 時以 rerun 為最終採用（rerun 多半是 HIGHSD 高變異後的
    複測），原始版本保留但標為非最終。決策表可逐列覆寫此預設。
    """
    frame = table.copy()
    frame["最終採用"] = "N"
    frame["採用依據"] = ""

    for key, group in frame.groupby("最終列比對Key(輔助)"):
        sources_in_group = set(group["來源檔案"])
        overrides = {
            source: decision
            for (animal, organ, source), decision in decisions.final_use.items()
            if f"{animal}|{organ}" == key and source in sources_in_group
        }
        chosen = [s for s, d in overrides.items() if d["最終採用"] == "Y"]

        if len(chosen) > 1:
            warnings.append(
                f"{key}：決策表指定了 {len(chosen)} 列為最終採用，"
                "同一動物＋臟器只能有一列。本次全部視為未指定，改用系統預設規則。"
            )
            chosen = []

        if chosen:
            target = chosen[0]
            basis = "決策表指定"
        else:
            reruns = group[group["版本"] == "rerun"]
            pool = reruns if not reruns.empty else group
            target = pool.iloc[-1]["來源檔案"] if len(pool) else group.iloc[-1]["來源檔案"]
            basis = "系統預設(rerun優先)" if not reruns.empty else "系統預設(唯一版本)"
            if len(pool) > 1:
                warnings.append(
                    f"{key}：有 {len(pool)} 個同版本結果，預設採用最後一個 run "
                    f"({target})。如需改採其他列，請在決策表「最終採用覆核」指定。"
                )

        mask = (frame["最終列比對Key(輔助)"] == key)
        frame.loc[mask & (frame["來源檔案"] == target), "最終採用"] = "Y"
        frame.loc[mask & (frame["來源檔案"] == target), "採用依據"] = basis

        for source, decision in overrides.items():
            note = (
                f"最終採用由 {decision['覆核者']} 於 {decision['覆核日期']} 指定為 "
                f"{decision['最終採用']}"
            )
            if decision.get("理由"):
                note += f"：{decision['理由']}"
            row_mask = mask & (frame["來源檔案"] == source)
            frame.loc[row_mask, "備註"] = frame.loc[row_mask, "備註"].map(
                lambda existing: _append_note(existing, note)
            )

    frame.loc[frame["最終採用"] == "N", "列標記"] = STYLE_NOT_FINAL
    return frame


def _finalise_highsd(table: pd.DataFrame, decisions: DecisionSet,
                     config: StudyConfig, nd_label: str,
                     warnings: list[str]) -> pd.DataFrame:
    """套用 ND 判定與 HIGHSD 處理規則。

    HIGHSD=Y 且最終採用、且該 key 無 rerun 時，系統【不會】自行選孔 ——
    匯出檔未附擴增曲線，無法客觀判斷哪一孔正確。依呈現結果分兩種標記：
    可定量 -> 請人工複核；ND -> 屬預期現象（低濃度時孔間微小差異易觸發 HIGHSD）。
    """
    frame = table.copy()
    keys_with_rerun = set(frame.loc[frame["版本"] == "rerun", "最終列比對Key(輔助)"])

    presentation: list[Any] = []
    review_state: list[str] = []
    styles: list[str] = []
    notes: list[str] = []
    quantities: list[Any] = []

    for record in frame.to_dict("records"):
        quantity = record["Quantity Mean 定量平均值"]
        lloq = record["LLOQ(該run標準曲線最後一點,pg)"]
        note = record["備註"]
        state = ""
        style = record["列標記"]

        key = record["最終列比對Key(輔助)"]
        decision_key = (record["動物編號"], record["臟器代碼"], record["來源檔案"])
        highsd_decision = decisions.highsd_well.get(decision_key)

        # 人工選孔：只在有決策時才動 Quantity Mean
        if highsd_decision:
            quantity = _apply_well_choice(record, highsd_decision["採用方式"], quantity)
            state = (
                f"已人工複核：採用{highsd_decision['採用方式']}"
                f"（{highsd_decision['覆核者']} / {highsd_decision['覆核日期']}）"
            )
            if highsd_decision.get("理由"):
                state += f"：{highsd_decision['理由']}"
            note = _append_note(note, state)

        # ND 判定
        if quantity is None or lloq is None:
            shown: Any = quantity
        elif bool(config.reporting.get("nd_below_lloq", True)) and quantity < lloq:
            shown = nd_label
        else:
            shown = quantity

        is_final = record["最終採用"] == "Y"
        needs_attention = (
            record["HIGHSD"] == "Y" and is_final
            and key not in keys_with_rerun and not highsd_decision
        )
        if needs_attention:
            if shown == nd_label:
                state = "屬預期現象（ND，不需複測）"
                note = _append_note(note, (
                    "兩重複孔位差異過大(HIGHSD=Y)但結果為 ND：濃度極低時孔位間微小差異"
                    "易被儀器內部比對放大而觸發，屬機器內部註記，不需複測。"
                ))
                style = STYLE_EXPECTED_ND
            else:
                state = "請人工複核"
                note = _append_note(note, (
                    "兩重複孔位差異過大(HIGHSD=Y)且無 rerun 複測，系統未自行選孔"
                    "（匯出檔未附擴增曲線圖形，無法客觀判斷）。"
                    "Quantity Mean 暫以儀器兩孔平均值呈現；"
                    "請於決策表「HIGHSD單孔採用」指定採用孔位。"
                ))
                style = STYLE_MANUAL_REVIEW

        presentation.append(shown)
        review_state.append(state)
        styles.append(style)
        notes.append(note)
        quantities.append(quantity)

    frame["Quantity Mean 定量平均值"] = quantities
    frame["呈現結果(低於LLOQ標示ND)"] = presentation
    frame["複核狀態"] = review_state
    frame["列標記"] = styles
    frame["備註"] = notes
    return frame


def _apply_well_choice(record: dict[str, Any], choice: str,
                       fallback: float | None) -> float | None:
    if choice == "孔位1":
        return record.get("孔位1-Quantity", fallback)
    if choice == "孔位2":
        return record.get("孔位2-Quantity", fallback)
    return fallback


def _append_note(existing: Any, addition: str) -> str:
    text = "" if existing is None or pd.isna(existing) else str(existing)
    if not text:
        return addition
    if addition in text:
        return text
    return f"{text}；{addition}"
