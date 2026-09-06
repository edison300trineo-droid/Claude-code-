"""血液 (臟器代碼 27) 的 LOB 背景校正。

背景：血液檢體由客戶動物實驗室採樣與前處理後送測，與本實驗室自行配製的基質
QC 對照組來源不同、干擾程度也不同，因此不以 QC 對照組推算門檻，改用 Predose
(用藥前) 血液檢體本身的「孔位層級」數值建立 LOB。用藥前理論上不應含人類 DNA
訊號，可視為與實際上機檢體同一前處理流程的背景對照。

注意自我參照性：Predose 列本身是建構此 LOB 的樣本，與該 LOB 比對時不具獨立性；
用藥後時間點才是樣本外比對。因此 Predose 的濃度與陽性隻數欄位刻意標為 "-"。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .classify import SampleClass
from .config import StudyConfig


@dataclass
class LOBResult:
    enabled: bool
    organ_code: str
    baseline_timepoint: str
    n_wells: int
    mean: float | None
    sd: float | None
    primary_multiplier: float
    reference_multiplier: float
    lob_primary: float | None
    lob_reference: float | None
    wells_detail: pd.DataFrame = field(default_factory=pd.DataFrame)
    comparison: pd.DataFrame = field(default_factory=pd.DataFrame)
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.enabled and self.lob_primary is not None


def compute_lob(wells: pd.DataFrame, consolidated: pd.DataFrame,
                config: StudyConfig) -> LOBResult:
    """由 Predose 血液孔位建立 LOB，並對所有血液列做比對判定。"""
    settings = config.lob
    enabled = bool(settings.get("enabled", False))
    organ_code = str(settings.get("organ_code", "27"))
    baseline_tp = str(settings.get("baseline_timepoint", "Predose"))
    k_primary = float(settings.get("primary_multiplier", 1.645))
    k_reference = float(settings.get("reference_multiplier", 2.0))
    min_n = int(settings.get("min_n", 20))

    result = LOBResult(
        enabled=enabled, organ_code=organ_code, baseline_timepoint=baseline_tp,
        n_wells=0, mean=None, sd=None, primary_multiplier=k_primary,
        reference_multiplier=k_reference, lob_primary=None, lob_reference=None,
    )
    if not enabled or consolidated.empty:
        return result

    blood = consolidated[
        (consolidated["臟器代碼"] == organ_code) & (consolidated["最終採用"] == "Y")
    ]
    if blood.empty:
        result.notes.append(f"沒有臟器代碼 {organ_code} 的最終採用資料，未計算 LOB。")
        return result

    baseline = blood[blood["採樣時間點(Time point)"] == baseline_tp]
    if baseline.empty:
        result.notes.append(
            f"找不到時間點為「{baseline_tp}」的血液檢體（可能是參考資料尚未提供採樣時間點），"
            "本次未建立 LOB，血液結果維持一般 ND 判定。"
        )
        return result

    detail = _baseline_wells(wells, baseline, organ_code)
    result.wells_detail = detail
    values = detail["Quantity (pg)"].dropna().astype(float)
    result.n_wells = int(len(values))

    if result.n_wells < 2:
        result.notes.append("Predose 血液有效孔位少於 2 個，無法計算 SD，未建立 LOB。")
        return result

    mean = float(values.mean())
    sd = float(values.std(ddof=1))
    result.mean, result.sd = mean, sd
    result.lob_primary = mean + k_primary * sd
    result.lob_reference = mean + k_reference * sd

    if result.n_wells < min_n:
        result.notes.append(
            f"目前 Predose 孔位數為 {result.n_wells}，低於建議之 ≥{min_n} 筆門檻，"
            "僅供內部排查參考；若要正式納入方法確效/SOP，建議另外補做達門檻的重複實驗。"
        )
    result.notes.append(
        f"LOB 採用 Mean + {k_primary}×SD = {result.lob_primary:.6f} pg（本次分析採用值）；"
        f"Mean + {k_reference}×SD = {result.lob_reference:.6f} pg 僅供參考。"
    )

    result.comparison = _compare(blood, result, config)
    return result


def _baseline_wells(wells: pd.DataFrame, baseline: pd.DataFrame,
                    organ_code: str) -> pd.DataFrame:
    """取出 Predose 血液「最終採用列」對應的逐孔數值。"""
    allowed = {
        (row["動物編號"], row["來源檔案"]) for _, row in baseline.iterrows()
    }
    subset = wells[
        (wells["sample_class"] == SampleClass.ANIMAL.value)
        & (wells["organ_code"] == organ_code)
    ]
    rows: list[dict[str, Any]] = []
    for _, well in subset.iterrows():
        if (well["animal_id"], well["source_file"]) not in allowed:
            continue
        rows.append({
            "動物編號": well["animal_id"],
            "Sample Name": f"{well['animal_id']}_{organ_code}",
            "來源檔案(Run)": well["source_file"],
            "孔位": well["well"],
            "Quantity (pg)": well["quantity"],
        })
    return pd.DataFrame(rows).sort_values(["動物編號", "孔位"]).reset_index(drop=True) \
        if rows else pd.DataFrame(columns=["動物編號", "Sample Name", "來源檔案(Run)", "孔位", "Quantity (pg)"])


def _compare(blood: pd.DataFrame, result: LOBResult,
             config: StudyConfig) -> pd.DataFrame:
    """每一筆血液最終採用列與 LOB 比對，產生判定文字。"""
    nd_label = config.nd_label
    rows: list[dict[str, Any]] = []

    for _, row in blood.iterrows():
        shown = row["呈現結果(低於LLOQ標示ND)"]
        quantity = row["Quantity Mean 定量平均值"]
        is_baseline = row["採樣時間點(Time point)"] == result.baseline_timepoint

        verdict_primary = _verdict(shown, quantity, result.lob_primary, nd_label,
                                   result.primary_multiplier)
        verdict_reference = _verdict(shown, quantity, result.lob_reference, nd_label,
                                     result.reference_multiplier)

        note = ""
        if is_baseline:
            note = (
                f"注意：本列為 {result.baseline_timepoint}，屬於建構本 LOB 的樣本之一，"
                "與此 LOB 比對時具自我參照性。"
            )

        rows.append({
            "動物編號": row["動物編號"],
            "Sample Name": row["Sample Name"],
            "性別": row["性別(Sex)"],
            "採樣時間點": row["採樣時間點(Time point)"],
            "來源檔案(Run)": row["來源檔案"],
            "Quantity Mean (pg)": quantity,
            "該Run LLOQ (pg)": row["LLOQ(該run標準曲線最後一點,pg)"],
            "呈現結果(統整數據)": shown,
            f"LOB({result.primary_multiplier}×SD) (pg)": result.lob_primary,
            f"LOB({result.reference_multiplier}×SD) (pg)": result.lob_reference,
            f"與LOB({result.primary_multiplier}×SD)比較判定": verdict_primary,
            f"與LOB({result.reference_multiplier}×SD)比較判定": verdict_reference,
            "備註": note,
        })

    return pd.DataFrame(rows)


def _verdict(shown: Any, quantity: Any, lob: float | None, nd_label: str,
             multiplier: float) -> str:
    if shown == nd_label:
        return "ND(低於LLOQ)，已排除"
    if lob is None or quantity is None:
        return "無法判定"
    if float(quantity) >= lob:
        return f"高於背景(≥LOB {multiplier}×SD)，較可能為真實訊號，建議保留並依一般流程覆核"
    return (
        f"位於背景範圍內(<LOB {multiplier}×SD)，與背景訊號無法區分，"
        "建議視為背景/假陽性，非真實生物分布訊號"
    )


def is_positive(shown: Any, quantity: Any, lob: float | None, nd_label: str,
                require_non_nd: bool = True) -> bool:
    """LOB 校正後的陽性判定：需同時通過『非 ND』與『≥ LOB』。"""
    if require_non_nd and shown == nd_label:
        return False
    if quantity is None or lob is None:
        return False
    try:
        return float(quantity) >= lob
    except (TypeError, ValueError):
        return False
