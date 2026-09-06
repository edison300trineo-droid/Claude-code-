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
    n_points: int
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

    if len(points) < 3:
        notes.append("標準品有效孔位少於 3 點，無法回歸。")
        return StandardCurve(
            source_file, None, None, None, None, len(points), lloq, lloq_point,
            False, False, notes,
        )

    x = np.log10(points["quantity"].astype(float).to_numpy())
    y = points["ct"].astype(float).to_numpy()
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
        n_points=len(points),
        lloq=lloq,
        lloq_point=lloq_point,
        r2_pass=r2_pass,
        efficiency_pass=eff_pass,
        notes=notes,
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
