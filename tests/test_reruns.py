"""rerun 版本判定 —— 兩種機制並存，而且可能出現在同一塊盤上。"""

import pandas as pd
import pytest

from qpcr_biod.decisions import SHEET_FINAL, create_template, load_decisions
from qpcr_biod.pipeline import run_pipeline


def _versions(table: pd.DataFrame, key: str) -> pd.DataFrame:
    return table[table["最終列比對Key(輔助)"] == key].sort_values("Sample Name")


def test_same_plate_named_rerun_becomes_its_own_row(study_dir, config):
    """1014_22 與 1014_22_re 在同一個原始檔內，必須各自成列而非併成一筆。"""
    table = run_pipeline(config).consolidated
    versions = _versions(table, "1014|22")

    assert len(versions) == 2
    assert list(versions["Sample Name"]) == ["1014_22", "1014_22_re"]
    assert list(versions["版本"]) == ["原始", "Rerun"]
    # 每一列都只含自己的兩個孔位，沒有把四孔混在一起
    assert list(versions["孔位數"]) == [2, 2]
    assert versions.iloc[0]["Quantity Mean 定量平均值"] != versions.iloc[1]["Quantity Mean 定量平均值"]


def test_same_plate_rerun_wins_over_its_original(study_dir, config):
    table = run_pipeline(config).consolidated
    versions = _versions(table, "1014|22")
    final = versions[versions["最終採用"] == "Y"]

    assert len(final) == 1
    assert final.iloc[0]["Sample Name"] == "1014_22_re"
    assert final.iloc[0]["rerun判定依據"] == "樣品名稱標記"


def test_original_on_a_shared_plate_is_not_mislabelled_as_rerun(study_dir, config):
    """同盤只要有一個 _re，不能讓整個檔案的該檢體都被標成 rerun。"""
    table = run_pipeline(config).consolidated
    original = _versions(table, "1014|22").iloc[0]
    assert original["版本"] == "原始"
    assert original["rerun判定依據"] == ""


def test_later_run_rerun_is_detected_by_position(study_dir, config):
    """1011_03 在兩個檔案各出現一次，較晚的那個是 rerun。"""
    table = run_pipeline(config).consolidated
    versions = _versions(table, "1011|03")

    assert len(versions) == 2
    rerun = versions[versions["版本"] == "Rerun"].iloc[0]
    assert rerun["rerun判定依據"] == "較晚的run"
    assert rerun["最終採用"] == "Y"
    assert rerun["來源檔案"] == "20260824_BD-TS-20260701_07_data.xls"


def test_every_key_has_exactly_one_final_row(study_dir, config):
    table = run_pipeline(config).consolidated
    counts = table[table["最終採用"] == "Y"].groupby("最終列比對Key(輔助)").size()
    assert (counts == 1).all(), f"以下 key 的最終採用列數不是 1：\n{counts[counts != 1]}"


def test_decision_can_target_one_version_on_a_shared_plate(study_dir, config):
    """同檔多版本時，決策表用 Sample Name 指定要哪一個。"""
    path = create_template(config.path("decisions_file"))
    sheets = pd.read_excel(path, sheet_name=None, dtype=object)
    sheets[SHEET_FINAL] = pd.DataFrame([{
        "動物編號": "1014", "臟器代碼": "22",
        "來源檔案": "20260818_BD-TS-20260701_01_data.xls",
        "Sample Name(同檔多版本時填)": "1014_22",
        "最終採用(Y/N)": "Y",
        "覆核者": "李研究員", "覆核日期": "2026-09-06", "理由": "複測上樣量有疑慮",
    }])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)

    table = run_pipeline(config).consolidated
    final = _versions(table, "1014|22")
    final = final[final["最終採用"] == "Y"]

    assert len(final) == 1
    assert final.iloc[0]["Sample Name"] == "1014_22", "決策表應能覆寫預設的 rerun 優先規則"
    assert final.iloc[0]["採用依據"] == "決策表指定"
    assert "李研究員" in final.iloc[0]["備註"]


def test_decision_without_sample_name_still_works_when_unambiguous(study_dir, config):
    """單一版本的檔案不需要填 Sample Name。"""
    path = create_template(config.path("decisions_file"))
    sheets = pd.read_excel(path, sheet_name=None, dtype=object)
    sheets[SHEET_FINAL] = pd.DataFrame([{
        "動物編號": "1011", "臟器代碼": "03",
        "來源檔案": "20260818_BD-TS-20260701_01_data.xls",
        "Sample Name(同檔多版本時填)": "",
        "最終採用(Y/N)": "Y",
        "覆核者": "李研究員", "覆核日期": "2026-09-06", "理由": "",
    }])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)

    loaded = load_decisions(path)
    assert ("1011", "03", "20260818_BD-TS-20260701_01_data.xls", "") in loaded.final_use

    table = run_pipeline(config).consolidated
    final = _versions(table, "1011|03")
    final = final[final["最終採用"] == "Y"].iloc[0]
    assert final["來源檔案"] == "20260818_BD-TS-20260701_01_data.xls"
