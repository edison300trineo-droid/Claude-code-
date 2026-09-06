"""Organ Mean±SD 彙整報表（供試驗報告直接引用）。

統計規則：Mean/SD 僅納入可定量(非 ND)數值；ND 檢體只記數量於 Remarks，
不以 LLOQ/2 或任何值代入估算 —— 代入會把偵測不到的東西變成一個數字。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .config import StudyConfig
from .lob import LOBResult, is_positive


@dataclass
class SummaryTables:
    organ_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    blood_lob_adjusted: pd.DataFrame = field(default_factory=pd.DataFrame)
    subsets: dict[str, pd.DataFrame] = field(default_factory=dict)
    dot_plot: pd.DataFrame = field(default_factory=pd.DataFrame)
    notes: list[str] = field(default_factory=list)


def build_summaries(consolidated: pd.DataFrame, lob: LOBResult,
                    config: StudyConfig) -> SummaryTables:
    tables = SummaryTables()
    if consolidated.empty:
        return tables

    final = consolidated[consolidated["最終採用"] == "Y"].copy()
    tables.organ_summary = _organ_mean_sd(final, config)

    if lob.usable:
        tables.blood_lob_adjusted = _blood_lob_table(final, lob, config)
    elif lob.enabled and lob.notes:
        tables.notes.extend(lob.notes)

    for name, spec in (config.summary.get("delivery_subsets") or {}).items():
        codes = [str(c) for c in (spec.get("organ_codes") or [])]
        subset = final[final["臟器代碼"].isin(codes)] if codes else final
        tables.subsets[name] = _organ_mean_sd(subset, config)
        if spec.get("note"):
            tables.notes.append(f"{name}：{spec['note']}")

    tables.dot_plot = _dot_plot_data(final, config)
    return tables


def _organ_mean_sd(final: pd.DataFrame, config: StudyConfig) -> pd.DataFrame:
    """以臟器為主體、男女分列、依時間點排序的 Mean±SD 表。"""
    if final.empty:
        return pd.DataFrame()

    nd_label = config.nd_label
    unit = config.unit
    include_nd = bool(config.summary.get("include_nd_in_stats", False))
    rows: list[dict[str, Any]] = []

    grouped = final.groupby(
        ["臟器代碼", "臟器名稱", "性別(Sex)", "採樣時間點(Time point)"], dropna=False
    )
    for (code, organ, sex, timepoint), group in grouped:
        shown = group["呈現結果(低於LLOQ標示ND)"]
        nd_count = int((shown == nd_label).sum())
        if include_nd:
            values = pd.to_numeric(group["Quantity Mean 定量平均值"], errors="coerce").dropna()
        else:
            quantifiable = group[shown != nd_label]
            values = pd.to_numeric(
                quantifiable["Quantity Mean 定量平均值"], errors="coerce"
            ).dropna()

        n_total = len(group)
        remarks: list[str] = []
        if nd_count:
            remarks.append(f"{nd_count}/{n_total} samples ND (not included in Mean/SD)")
        if values.empty:
            remarks.append("All samples ND; no quantifiable value")

        rows.append({
            "Organ code": code,
            "Organ": organ,
            "Sex": sex or "(未提供)",
            "Time point": timepoint or "(未提供)",
            "N (total)": n_total,
            "N (quantifiable)": int(len(values)),
            f"Mean ({unit})": float(values.mean()) if not values.empty else None,
            f"SD ({unit})": float(values.std(ddof=1)) if len(values) > 1 else None,
            "Remarks": "; ".join(remarks),
        })

    frame = pd.DataFrame(rows)
    frame["_tp"] = frame["Time point"].map(config.timepoint_sort_key)
    frame = frame.sort_values(["Organ code", "Sex", "_tp"]).drop(columns="_tp")
    return frame.reset_index(drop=True)


def _blood_lob_table(final: pd.DataFrame, lob: LOBResult,
                     config: StudyConfig) -> pd.DataFrame:
    """血液的 LOB-Adjusted Calculation 子表。

    陽性需同時通過『非 ND』與『≥ LOB』。Predose 本身是 LOB 定義組，其濃度與
    陽性隻數欄位標為 "-" 並附說明，不予計算。
    """
    nd_label = config.nd_label
    unit = config.unit
    require_non_nd = bool(config.lob.get("positive_requires_non_nd", True))
    exclude_baseline = bool(config.lob.get("exclude_baseline_from_stats", True))

    blood = final[final["臟器代碼"] == lob.organ_code]
    if blood.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for (sex, timepoint), group in blood.groupby(
        ["性別(Sex)", "採樣時間點(Time point)"], dropna=False
    ):
        is_baseline = timepoint == lob.baseline_timepoint
        positives = [
            is_positive(row["呈現結果(低於LLOQ標示ND)"],
                        row["Quantity Mean 定量平均值"], lob.lob_primary,
                        nd_label, require_non_nd)
            for _, row in group.iterrows()
        ]
        values = pd.to_numeric(
            group.loc[positives, "Quantity Mean 定量平均值"], errors="coerce"
        ).dropna()

        if is_baseline and exclude_baseline:
            rows.append({
                "Organ": lob.organ_code + " Blood",
                "Sex": sex or "(未提供)",
                "Time point": timepoint or "(未提供)",
                "N (total)": len(group),
                "Positive (≥LOB & non-ND)": "-",
                f"Mean ({unit})": "-",
                f"SD ({unit})": "-",
                "Remarks": (
                    f"本時間點為 LOB 定義組（LOB 由此組孔位資料推得），"
                    "具自我參照性，故不計算陽性隻數與 Mean/SD。"
                ),
            })
            continue

        rows.append({
            "Organ": lob.organ_code + " Blood",
            "Sex": sex or "(未提供)",
            "Time point": timepoint or "(未提供)",
            "N (total)": len(group),
            "Positive (≥LOB & non-ND)": int(sum(positives)),
            f"Mean ({unit})": float(values.mean()) if not values.empty else None,
            f"SD ({unit})": float(values.std(ddof=1)) if len(values) > 1 else None,
            "Remarks": (
                f"LOB = {lob.lob_primary:.6f} pg (Mean + {lob.primary_multiplier}×SD, "
                f"n={lob.n_wells} Predose wells); 陽性需同時非 ND 且 ≥ LOB。"
            ),
        })

    frame = pd.DataFrame(rows)
    frame["_tp"] = frame["Time point"].map(config.timepoint_sort_key)
    return frame.sort_values(["Sex", "_tp"]).drop(columns="_tp").reset_index(drop=True)


def _dot_plot_data(final: pd.DataFrame, config: StudyConfig) -> pd.DataFrame:
    """繪圖用資料。只整理數值，不產生圖表本身。"""
    codes = [str(c) for c in (config.dot_plot.get("organ_codes") or [])]
    if not codes:
        return pd.DataFrame()

    nd_label = config.nd_label
    substitute = str(config.dot_plot.get("nd_plot_substitute", "lloq/2"))
    subset = final[final["臟器代碼"].isin(codes)]
    if subset.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for _, row in subset.iterrows():
        shown = row["呈現結果(低於LLOQ標示ND)"]
        lloq = row["LLOQ(該run標準曲線最後一點,pg)"]
        quantity = row["Quantity Mean 定量平均值"]
        if shown == nd_label:
            plot_value = (float(lloq) / 2.0) if (substitute == "lloq/2" and lloq) else None
        else:
            plot_value = float(quantity) if quantity is not None else None
        rows.append({
            "臟器代碼": row["臟器代碼"],
            "臟器名稱": row["臟器名稱"],
            "性別": row["性別(Sex)"] or "(未提供)",
            "採樣時間點": row["採樣時間點(Time point)"] or "(未提供)",
            "動物編號": row["動物編號"],
            "原始測得平均值(pg)": quantity,
            "呈現結果": shown,
            "該筆LLOQ(pg)": lloq,
            f"建議繪圖數值(ND以{substitute}代入)": plot_value,
        })

    frame = pd.DataFrame(rows)
    frame["_tp"] = frame["採樣時間點"].map(config.timepoint_sort_key)
    return frame.sort_values(["臟器代碼", "性別", "_tp", "動物編號"]) \
        .drop(columns="_tp").reset_index(drop=True)
