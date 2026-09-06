"""標準曲線回歸、LLOQ、與允收判定。"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .classify import SampleClass
from .config import StudyConfig


@dataclass
class StandardCurve:
    """單一 run 的標準曲線。"""

    source_file: str
    slope: float | None
    intercept: float | None
    r_squared: float | None
    efficiency: float | None
    n_points: int          # 實際參與回歸的點數（依 fit_basis 為標準點數或孔位數）
    lloq: float | None
    lloq_point: str
    r2_pass: bool
    efficiency_pass: bool
    notes: list[str]

    @property
    def accepted(self) -> bool:
        return self.r2_pass and self.efficiency_pass

    @property
    def verdict(self) -> str:
        if self.slope is None:
            return "無法計算（標準品點數不足）"
        return "通過" if self.accepted else "請覆核"

    def quantity_from_ct(self, ct: float | None) -> float | None:
        """由 Ct 回推濃度，用於 NTC 等儀器未給 Quantity 的孔位。"""
        if ct is None or self.slope is None or self.intercept is None:
            return None
        if math.isnan(ct) or self.slope == 0:
            return None
        return float(10 ** ((ct - self.intercept) / self.slope))


def fit_standard_curve(wells: pd.DataFrame, source_file: str,
                       config: StudyConfig) -> StandardCurve:
    """對單一 run 的標準品孔位做 Ct vs log10(Quantity) 線性回歸。"""
    notes: list[str] = []
    subset = wells[
        (wells["source_file"] == source_file)
        & (wells["sample_class"] == SampleClass.STANDARD.value)
    ]

    points = subset.dropna(subset=["ct"])
    points = points[points["quantity"].notna() & (points["quantity"] > 0)]

    lloq_point = config.lloq_point
    lloq = _lloq_for_run(subset, config, notes)
    _check_nominal_concentrations(subset, config, notes)

    basis = config.fit_basis
    if basis == "point_mean":
        # 每個標準點取 Ct 平均後回歸，n = 標準點數。
        # 平衡設計下斜率/截距與逐孔回歸相同，但 R² 不含孔間變異。
        grouped = points.groupby("std_point", dropna=True).agg(
            quantity=("quantity", "first"), ct=("ct", "mean")
        )
        x_values = grouped["quantity"].astype(float).to_numpy()
        y_values = grouped["ct"].astype(float).to_numpy()
    elif basis == "well":
        x_values = points["quantity"].astype(float).to_numpy()
        y_values = points["ct"].astype(float).to_numpy()
    else:
        notes.append(
            f"設定檔的 fit_basis 值「{basis}」無法辨識，本次改用 point_mean。"
        )
        grouped = points.groupby("std_point", dropna=True).agg(
            quantity=("quantity", "first"), ct=("ct", "mean")
        )
        x_values = grouped["quantity"].astype(float).to_numpy()
        y_values = grouped["ct"].astype(float).to_numpy()

    n_used = len(x_values)
    if n_used < 3:
        notes.append(f"標準曲線有效點數為 {n_used}，少於 3 點，無法回歸。")
        return StandardCurve(
            source_file, None, None, None, None, n_used, lloq, lloq_point,
            False, False, notes,
        )

    x = np.log10(x_values)
    y = y_values
    slope, intercept = np.polyfit(x, y, 1)

    predicted = slope * x + intercept
    ss_res = float(np.sum((y - predicted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else None

    efficiency = None
    if slope != 0:
        efficiency = (10 ** (-1.0 / slope) - 1.0) * 100.0

    min_r2 = float(config.standard_curve.get("min_r_squared", 0.98))
    eff_low, eff_high = config.standard_curve.get("efficiency_range", [85.0, 115.0])

    r2_pass = r_squared is not None and r_squared >= min_r2
    eff_pass = efficiency is not None and eff_low <= efficiency <= eff_high

    if not r2_pass:
        notes.append(f"R² 未達允收標準 (≥{min_r2})。")
    if not eff_pass:
        notes.append(f"PCR 效率未落在允收範圍 ({eff_low}–{eff_high}%)。")

    return StandardCurve(
        source_file=source_file,
        slope=float(slope),
        intercept=float(intercept),
        r_squared=None if r_squared is None else float(r_squared),
        efficiency=None if efficiency is None else float(efficiency),
        n_points=n_used,
        lloq=lloq,
        lloq_point=lloq_point,
        r2_pass=r2_pass,
        efficiency_pass=eff_pass,
        notes=notes,
    )


def _check_nominal_concentrations(standards: pd.DataFrame, config: StudyConfig,
                                  notes: list[str]) -> None:
    """比對設定檔的稀釋序列與儀器實際輸出的標稱濃度。

    回歸本身用的是儀器的 Quantity 欄，所以序列填錯不會讓曲線算錯；但它會讓
    LLOQ 的退回值、以及已知濃度回推 QC 的標稱值靜默地錯掉。這個檢查把那種
    錯誤變成一則明確的警告，而不是等到有人核對報告時才發現。
    """
    tolerance = config.nominal_tolerance
    mismatches: list[str] = []

    for std_point, group in standards.groupby("std_point", dropna=True):
        expected = config.nominal_concentration(str(std_point))
        if expected is None:
            notes.append(
                f"標準點 {std_point} 出現在資料中，但設定檔的 nominal_concentrations "
                "沒有這一點，請補上。"
            )
            continue
        actual_values = group["quantity"].dropna()
        if actual_values.empty:
            continue
        actual = float(actual_values.iloc[0])
        scale = max(abs(expected), abs(actual), 1e-12)
        if abs(actual - expected) / scale > tolerance:
            mismatches.append(f"{std_point}（設定檔 {expected:g}，儀器 {actual:g}）")

    if mismatches:
        notes.append(
            "設定檔的標準品標稱濃度與儀器實際輸出不一致："
            + "、".join(mismatches)
            + "。標準曲線回歸使用的是儀器數值，不受影響；但 LLOQ 退回值與"
            "已知濃度回推 QC 會用到設定檔數值，請更正 config 的 "
            "nominal_concentrations。"
        )


def _lloq_for_run(standards: pd.DataFrame, config: StudyConfig,
                  notes: list[str]) -> float | None:
    """LLOQ = 該 run 標準曲線最後一點（預設 STD08）的濃度。

    優先取該 run 實際上機的標準點，缺席時才退回設定檔標稱值並註記。
    """
    lloq_point = config.lloq_point
    matched = standards[standards["std_point"] == lloq_point]
    quantities = matched["quantity"].dropna()
    if not quantities.empty:
        return float(quantities.iloc[0])

    fallback = config.nominal_concentration(lloq_point)
    if fallback is not None:
        notes.append(
            f"此 run 未偵測到 {lloq_point} 孔位，LLOQ 改用設定檔標稱值 {fallback} pg，請覆核。"
        )
        return float(fallback)

    notes.append(f"此 run 無法決定 LLOQ（找不到 {lloq_point}）。")
    return None


def fit_all_curves(wells: pd.DataFrame, config: StudyConfig) -> dict[str, StandardCurve]:
    """每個原始檔（= 一個 run）各配一條標準曲線。"""
    return {
        source_file: fit_standard_curve(wells, source_file, config)
        for source_file in sorted(wells["source_file"].unique())
    }
