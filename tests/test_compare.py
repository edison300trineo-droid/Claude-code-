"""對帳功能測試 —— 導入驗收會用到的東西，必須先確定它抓得出差異。"""

import pandas as pd
import pytest

from qpcr_biod.compare import compare_tables, load_previous, write_comparison_report
from qpcr_biod.pipeline import run_pipeline


def test_identical_tables_compare_clean(study_dir, config):
    table = run_pipeline(config).consolidated
    result = compare_tables(table, table)
    assert result.is_clean
    assert result.matched == len(table)
    assert result.differing_rows == 0


def test_numeric_difference_is_detected(study_dir, config):
    table = run_pipeline(config).consolidated
    modified = table.copy()
    column = "Quantity Mean 定量平均值"
    modified.loc[modified.index[0], column] = float(modified.iloc[0][column]) * 1.05

    result = compare_tables(table, modified)
    assert not result.is_clean
    assert result.differing_rows == 1
    assert result.differences.iloc[0]["欄位"] == column
    assert "+5" in result.differences.iloc[0]["差異"]


def test_tiny_float_noise_is_tolerated(study_dir, config):
    """Excel 存取造成的尾差不該被當成真差異。"""
    table = run_pipeline(config).consolidated
    modified = table.copy()
    column = "Quantity Mean 定量平均值"
    modified.loc[modified.index[0], column] = (
        float(modified.iloc[0][column]) * (1 + 1e-9)
    )
    assert compare_tables(table, modified).is_clean


def test_nd_flag_difference_is_detected(study_dir, config):
    table = run_pipeline(config).consolidated
    modified = table.copy()
    column = "呈現結果(低於LLOQ標示ND)"
    target = modified.index[modified[column] == "ND"][0]
    modified.loc[target, column] = 0.5

    result = compare_tables(table, modified)
    diffs = result.differences
    assert (diffs["欄位"] == column).any()


def test_missing_and_extra_rows_are_reported(study_dir, config):
    table = run_pipeline(config).consolidated
    result = compare_tables(table.iloc[1:], table.iloc[:-1])
    assert len(result.only_in_new) == 1   # 舊表少了第一列
    assert len(result.only_in_old) == 1   # 新表少了最後一列


def test_leading_zeros_align_across_formats(study_dir, config):
    """舊表若把 '0006' 存成數字 6，仍必須對得上。"""
    table = run_pipeline(config).consolidated
    as_numbers = table.copy()
    as_numbers["動物編號"] = as_numbers["動物編號"].map(int)
    as_numbers["臟器代碼"] = as_numbers["臟器代碼"].map(int)
    assert compare_tables(as_numbers, table).is_clean


def test_report_is_written_with_expected_sheets(study_dir, config, tmp_path):
    table = run_pipeline(config).consolidated
    modified = table.copy()
    modified.loc[modified.index[0], "Quantity Mean 定量平均值"] = 99.0

    path = write_comparison_report(tmp_path / "對照報告.xlsx",
                                   compare_tables(table, modified))
    sheets = pd.ExcelFile(path).sheet_names
    for expected in ("對照摘要", "逐欄差異", "只在既有表", "只在pipeline", "備註"):
        assert expected in sheets


def test_wrong_sheet_name_gives_a_helpful_error(study_dir, config, tmp_path):
    path = tmp_path / "old.xlsx"
    pd.DataFrame({"x": [1]}).to_excel(path, sheet_name="其他", index=False)
    with pytest.raises(ValueError, match="找不到分頁"):
        load_previous(path, "統整數據")


def test_sheet_without_key_columns_is_rejected(study_dir, config, tmp_path):
    path = tmp_path / "old.xlsx"
    pd.DataFrame({"隨便": [1]}).to_excel(path, sheet_name="統整數據", index=False)
    with pytest.raises(ValueError, match="缺少對照所需的欄位"):
        load_previous(path, "統整數據")
