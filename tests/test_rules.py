"""判定規則的邊界測試 —— 這些是報告數字會不會出錯的地方。"""

import math

import pandas as pd
import pytest

from qpcr_biod.classify import SampleClass, annotate_wells, classify_sample
from qpcr_biod.curves import fit_standard_curve
from qpcr_biod.lob import is_positive
from qpcr_biod.qc import evaluate_qc


def _wells(rows: list[dict]) -> pd.DataFrame:
    base = {
        "source_file": "run.xls", "run_end_time": "2026-08-18 15:00:52 PM CST",
        "well": "A1", "sample_name": "", "target_name": "t", "task": "UNKNOWN",
        "ct": None, "ct_mean": None, "ct_sd": None, "quantity": None,
        "quantity_mean": None, "quantity_sd": None, "highsd": None,
    }
    return pd.DataFrame([{**base, **row} for row in rows])


# --- 檢體分類 ---------------------------------------------------------------

def test_std_name_with_unknown_task_is_accuracy_qc_not_a_curve_point(config):
    """STD08 以 UNKNOWN 重跑是回推 QC，若誤當成曲線點會污染標準曲線。"""
    as_standard = classify_sample("STD08", "STANDARD", config)
    as_unknown = classify_sample("STD08", "UNKNOWN", config)
    assert as_standard.sample_class is SampleClass.STANDARD
    assert as_unknown.sample_class is SampleClass.STD_ACCURACY


def test_matrix_control_is_not_mistaken_for_an_animal(config):
    identity = classify_sample("27 mouse blood cell pellet DNA", "UNKNOWN", config)
    assert identity.sample_class is SampleClass.MATRIX_QC
    assert identity.organ_code == "27"


def test_sensitivity_control_nominal_is_parsed(config):
    identity = classify_sample("200pg sensitivity control", "UNKNOWN", config)
    assert identity.sample_class is SampleClass.SENSITIVITY_QC
    assert identity.nominal_concentration == 200.0


def test_animal_sample_parsing_keeps_leading_zeros(config):
    identity = classify_sample("0006_03", "UNKNOWN", config)
    assert identity.animal_id == "0006"
    assert identity.organ_code == "03"


# --- 標準曲線 ---------------------------------------------------------------

def _curve_wells(config, points: dict[str, float], ct_of) -> pd.DataFrame:
    rows = []
    for name, quantity in points.items():
        for _ in range(2):
            rows.append({
                "sample_name": name, "task": "STANDARD",
                "ct": ct_of(quantity), "quantity": quantity,
            })
    return annotate_wells(_wells(rows), config)


def test_curve_acceptance_passes_for_a_clean_curve(config):
    def ct_of(q):
        return 25.4 - 3.35 * math.log10(q)

    points = {"STD01": 10000, "STD02": 1000, "STD03": 100, "STD04": 10,
              "STD05": 1, "STD08": 0.128}
    curve = fit_standard_curve(_curve_wells(config, points, ct_of), "run.xls", config)

    assert curve.r_squared > 0.999
    assert 85 <= curve.efficiency <= 115
    assert curve.lloq == pytest.approx(0.128)
    assert curve.accepted and curve.verdict == "通過"


def test_curve_with_bad_efficiency_is_flagged(config):
    # slope -2.0 -> 效率約 216%，遠超允收上限
    def ct_of(q):
        return 20.0 - 2.0 * math.log10(q)

    points = {"STD01": 10000, "STD02": 1000, "STD03": 100, "STD08": 0.128}
    curve = fit_standard_curve(_curve_wells(config, points, ct_of), "run.xls", config)

    assert not curve.efficiency_pass
    assert curve.verdict == "請覆核"


def test_curve_with_too_few_points_does_not_fabricate_a_fit(config):
    def ct_of(q):
        return 25.4 - 3.35 * math.log10(q)

    curve = fit_standard_curve(_curve_wells(config, {"STD01": 10000}, ct_of),
                               "run.xls", config)
    assert curve.slope is None
    assert not curve.accepted


def test_lloq_falls_back_to_nominal_with_a_note_when_point_is_absent(config):
    def ct_of(q):
        return 25.4 - 3.35 * math.log10(q)

    points = {"STD01": 10000, "STD02": 1000, "STD03": 100, "STD04": 10}
    curve = fit_standard_curve(_curve_wells(config, points, ct_of), "run.xls", config)

    assert curve.lloq == pytest.approx(0.128)
    assert any("改用設定檔標稱值" in note for note in curve.notes)


# --- QC ---------------------------------------------------------------------

def _qc_setup(config, rows):
    wells = annotate_wells(_wells(rows), config)
    curves = {"run.xls": fit_standard_curve(
        _curve_wells(config, {"STD01": 10000, "STD02": 1000, "STD03": 100,
                              "STD08": 0.128},
                     lambda q: 25.4 - 3.35 * math.log10(q)),
        "run.xls", config)}
    return evaluate_qc(wells, curves, config)


def test_sensitivity_recovery_outside_range_is_flagged(config):
    points = _qc_setup(config, [
        {"sample_name": "200pg sensitivity control", "quantity": 130.0},
        {"sample_name": "20pg sensitivity control", "quantity": 19.0},
    ])
    by_name = {p.sample_name: p for p in points}
    assert by_name["200pg sensitivity control"].verdict == "請覆核"   # 65%
    assert by_name["20pg sensitivity control"].verdict == "通過"       # 95%


def test_ntc_background_below_lloq_passes(config):
    points = _qc_setup(config, [
        {"sample_name": "NTC", "task": "NTC", "quantity": 0.0036, "ct": 33.9},
    ])
    ntc = [p for p in points if p.qc_type.startswith("陰性對照")][0]
    assert ntc.verdict == "通過"
    assert "正常背景訊號" in ntc.note


def test_ntc_at_or_above_lloq_is_flagged(config):
    points = _qc_setup(config, [
        {"sample_name": "NTC", "task": "NTC", "quantity": 0.128, "ct": 28.4},
    ])
    ntc = [p for p in points if p.qc_type.startswith("陰性對照")][0]
    assert ntc.verdict == "請覆核"


def test_matrix_control_is_recorded_without_a_recovery_verdict(config):
    points = _qc_setup(config, [
        {"sample_name": "27 mouse blood cell pellet DNA", "quantity": 0.1368},
    ])
    matrix = [p for p in points if p.qc_type == "臟器基質QC對照組"][0]
    assert matrix.recovery_percent is None
    assert matrix.verdict == "—"


# --- LOB 陽性判定 -----------------------------------------------------------

@pytest.mark.parametrize(
    "shown, quantity, expected, why",
    [
        ("ND", 0.6, False, "ND 一律不算陽性，即使數值高於 LOB"),
        (0.6, 0.6, True, "非 ND 且 >= LOB"),
        (0.4, 0.4, False, "非 ND 但低於 LOB"),
        (0.51, 0.51, True, "剛好等於 LOB 視為陽性"),
        (None, None, False, "沒有數值不能判陽性"),
    ],
)
def test_lob_positive_requires_both_conditions(shown, quantity, expected, why):
    assert is_positive(shown, quantity, 0.51, "ND") is expected, why
