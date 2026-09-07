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


def test_key_escalates_to_sample_name_when_a_plate_holds_two_versions(study_dir, config):
    """同盤的 1014_22 與 1014_22_re 不能被當成同一筆對照。"""
    table = run_pipeline(config).consolidated
    result = compare_tables(table, table)

    assert result.is_clean
    assert result.matched == len(table), "升級鍵值後每一列都要能各自對到"
    assert any("已自動改用" in note for note in result.notes)


def test_escalated_key_still_detects_a_difference_on_one_version(study_dir, config):
    table = run_pipeline(config).consolidated
    modified = table.copy()
    target = modified.index[modified["Sample Name"] == "1014_22_re"][0]
    modified.loc[target, "Quantity Mean 定量平均值"] = 9.99

    result = compare_tables(table, modified)
    assert result.differing_rows == 1
    assert "1014_22_re" in result.differences.iloc[0]["對照鍵"]


def test_subset_run_reports_matching_values_separately_from_coverage(study_dir, config):
    """分階段驗收：只放部分原始檔時，值一致不該被講成「有差異」。"""
    table = run_pipeline(config).consolidated
    subset = table[table["來源檔案"].str.contains("_01_")]

    result = compare_tables(table, subset)
    assert result.values_match, "重疊的列值應完全一致"
    assert not result.coverage_matches
    assert not result.is_clean
    assert len(result.only_in_old) > 0
    assert len(result.only_in_new) == 0


# --- 混合型欄位（數值 或 ND 標記）------------------------------------------

def test_float_precision_in_the_result_column_is_not_a_difference(study_dir, config):
    """0.154890835285187 與 0.15489083528518677 是同一個數字，不是差異。

    既有活頁簿存的是 Excel 的 15 位表示，pipeline 由原始 float64 產生完整位數。
    純字串比對會把整批可定量結果都報成不一致。
    """
    table = run_pipeline(config).consolidated
    column = "呈現結果(低於LLOQ標示ND)"

    rounded = table.copy()
    numeric_rows = rounded.index[rounded[column].map(lambda v: isinstance(v, float))]
    assert len(numeric_rows) > 0, "需要至少一列可定量結果才測得到"
    for index in numeric_rows:
        rounded.loc[index, column] = float(f"{rounded.loc[index, column]:.15g}")

    result = compare_tables(table, rounded)
    assert result.values_match, (
        "浮點位數差異不該被判為不一致：\n"
        f"{result.differences.head().to_string() if not result.differences.empty else ''}"
    )


def test_a_real_change_in_the_result_column_is_still_detected(study_dir, config):
    table = run_pipeline(config).consolidated
    column = "呈現結果(低於LLOQ標示ND)"

    modified = table.copy()
    target = modified.index[modified[column].map(lambda v: isinstance(v, float))][0]
    modified.loc[target, column] = float(modified.loc[target, column]) * 1.05

    result = compare_tables(table, modified)
    assert result.differing_rows == 1
    assert result.differences.iloc[0]["欄位"] == column


def test_number_versus_nd_is_reported_as_a_label_mismatch(study_dir, config):
    """數值變成 ND（或反過來）是真正要抓的差異，訊息也要講得清楚。"""
    table = run_pipeline(config).consolidated
    column = "呈現結果(低於LLOQ標示ND)"

    modified = table.copy()
    target = modified.index[modified[column].map(lambda v: isinstance(v, float))][0]
    modified.loc[target, column] = "ND"

    result = compare_tables(table, modified)
    assert result.differing_rows == 1
    assert "標記不一致" in result.differences.iloc[0]["差異"]
