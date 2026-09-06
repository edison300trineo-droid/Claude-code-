"""QC 點位判定：敏感度對照回收率、臟器基質對照、NTC。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .classify import SampleClass
from .config import StudyConfig
from .curves import StandardCurve


@dataclass
class QCPoint:
    """一個 QC 點位的判定結果（對應「標準曲線與QC」分頁的一列）。"""

    source_file: str
    qc_type: str
    sample_name: str
    nominal: float | None
    measured: float | None
    recovery_percent: float | None
    verdict: str
    note: str = ""


def _mean_of(group: pd.DataFrame, column: str) -> float | None:
    values = group[column].dropna()
    if values.empty:
        return None
    return float(values.mean())


def evaluate_qc(wells: pd.DataFrame, curves: dict[str, StandardCurve],
                config: StudyConfig) -> list[QCPoint]:
    """產生所有 run 的 QC 點位明細。

    回收率判定「只」看敏感度對照組（依 EC 指示）。基質對照與 NTC 各有自己的
    判定規則，不套用回收率區間。已知濃度回推 QC 依設定預設不列入本表，
    但其原始孔位資料仍完整保留在 Raw_Import。
    """
    points: list[QCPoint] = []
    include_std_accuracy = bool(
        config.qc.get("std_accuracy_check", {}).get("include_in_qc_sheet", False)
    )
    low, high = config.qc.get("sensitivity_control", {}).get("recovery_range", [80.0, 120.0])

    for source_file in sorted(wells["source_file"].unique()):
        run_wells = wells[wells["source_file"] == source_file]
        curve = curves.get(source_file)

        # --- 敏感度對照組：唯一的回收率判定依據 ---
        sens = run_wells[run_wells["sample_class"] == SampleClass.SENSITIVITY_QC.value]
        for name, group in sens.groupby("sample_name"):
            nominal = _first_not_null(group, "nominal_concentration")
            measured = _mean_of(group, "quantity")
            recovery = None
            if nominal and measured is not None and nominal != 0:
                recovery = measured / nominal * 100.0
            if recovery is None:
                verdict = "無法計算"
            elif low <= recovery <= high:
                verdict = "通過"
            else:
                verdict = "請覆核"
            points.append(QCPoint(
                source_file, "敏感度對照", str(name), nominal, measured, recovery,
                verdict, f"允收範圍 {low}–{high}%",
            ))

        # --- 臟器基質 QC 對照組：記錄實測值，不做回收率判定 ---
        matrix = run_wells[run_wells["sample_class"] == SampleClass.MATRIX_QC.value]
        for name, group in matrix.groupby("sample_name"):
            organ_code = _first_not_null(group, "organ_code")
            organ_name = config.organ_name(str(organ_code)) if organ_code else ""
            points.append(QCPoint(
                source_file, "臟器基質QC對照組", str(name), None,
                _mean_of(group, "quantity"), None, "—",
                f"臟器代碼 {organ_code} {organ_name}；本項僅記錄基質背景，不套用回收率判定",
            ))

        # --- NTC：Alu 高敏感度方法，微量背景屬正常 ---
        ntc = run_wells[run_wells["sample_class"] == SampleClass.NTC.value]
        for name, group in ntc.groupby("sample_name"):
            measured = _mean_of(group, "quantity")
            if measured is None and curve is not None:
                # 儀器對 NTC 常不給 Quantity，改由標準曲線回推
                ct_mean = _mean_of(group, "ct")
                measured = curve.quantity_from_ct(ct_mean)
            points.append(QCPoint(
                source_file, "陰性對照(NTC)", str(name), None, measured, None,
                *_ntc_verdict(measured, curve, config),
            ))

        # --- 已知濃度回推 QC：預設不納入本表 ---
        if include_std_accuracy:
            accuracy = run_wells[run_wells["sample_class"] == SampleClass.STD_ACCURACY.value]
            for name, group in accuracy.groupby("sample_name"):
                nominal = _first_not_null(group, "nominal_concentration")
                measured = _mean_of(group, "quantity")
                recovery = (measured / nominal * 100.0) if nominal and measured else None
                points.append(QCPoint(
                    source_file, "已知濃度回推QC", str(name), nominal, measured,
                    recovery, "—", "依指示不作為允收判定依據，僅供追溯",
                ))

    return points


def _ntc_verdict(measured: float | None, curve: StandardCurve | None,
                 config: StudyConfig) -> tuple[str, str]:
    """NTC 判定：只有回推濃度 >= 該 run LLOQ 才算異常。"""
    rule_on = bool(config.qc.get("ntc", {}).get("abnormal_if_quantity_gte_lloq", True))
    if measured is None:
        return "通過", "未偵測到訊號 (Undetermined)"
    if not rule_on or curve is None or curve.lloq is None:
        return "—", "無法取得該 run LLOQ，未做判定"
    if measured >= curve.lloq:
        return "請覆核", (
            f"NTC 回推濃度 {measured:.6g} pg 已達該 run 標準曲線最後一點 "
            f"({curve.lloq_point} = {curve.lloq:g} pg)，需複核。"
        )
    return "通過", (
        "訊號未超過標準曲線最後一點，屬 Alu 高敏感度方法之正常背景訊號。"
    )


def _first_not_null(group: pd.DataFrame, column: str):
    values = group[column].dropna()
    return values.iloc[0] if not values.empty else None
