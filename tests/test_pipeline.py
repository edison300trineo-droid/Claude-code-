"""端到端測試：涵蓋同仁實際會走的流程。"""

from pathlib import Path

import pandas as pd
import pytest

from qpcr_biod.decisions import (
    SHEET_FINAL,
    SHEET_GROUP,
    SHEET_HIGHSD,
    SHEET_ORGAN,
    create_template,
    load_decisions,
)
from qpcr_biod.pipeline import run_pipeline


def _read(path: Path, sheet: str) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet, dtype=object)


def _row(table: pd.DataFrame, animal: str, organ: str) -> pd.Series:
    match = table[
        (table["動物編號"].astype(str) == animal)
        & (table["臟器代碼"].astype(str).str.zfill(2) == organ)
        & (table["最終採用"] == "Y")
    ]
    assert len(match) == 1, f"{animal}_{organ} 應該只有一列最終採用，實得 {len(match)}"
    return match.iloc[0]


def test_pipeline_runs_and_writes_all_sheets(study_dir, config):
    result = run_pipeline(config)
    assert result.output_path.is_file()
    assert result.run_count == 2

    sheets = pd.ExcelFile(result.output_path).sheet_names
    for expected in ("說明", "統整數據", "Organ Mean SD Summary", "標準曲線與QC",
                     "Raw_Import", "執行紀錄", "執行紀錄-輸入檔", "執行紀錄-警告"):
        assert expected in sheets


def test_rerun_supersedes_original_but_original_is_kept(study_dir, config):
    result = run_pipeline(config)
    table = result.consolidated
    versions = table[table["最終列比對Key(輔助)"] == "1011|03"]
    assert len(versions) == 2, "原始版本必須保留供追溯，不能被刪掉"
    assert set(versions["版本"]) == {"原始", "Rerun"}
    assert versions[versions["版本"] == "Rerun"].iloc[0]["最終採用"] == "Y"
    assert versions[versions["版本"] == "原始"].iloc[0]["最終採用"] == "N"


def test_nd_is_flagged_without_overwriting_measured_value(study_dir, config):
    result = run_pipeline(config)
    row = _row(result.consolidated, "0007", "27")
    assert row["呈現結果(低於LLOQ標示ND)"] == "ND"
    # 原始測得值必須完整保留，ND 只是報告呈現層級的註記
    assert float(row["原始測得平均值(未覆蓋)"]) > 0
    assert float(row["原始測得平均值(未覆蓋)"]) < float(row["LLOQ(該run標準曲線最後一點,pg)"])


def test_highsd_quantifiable_without_rerun_needs_manual_review(study_dir, config):
    result = run_pipeline(config)
    row = _row(result.consolidated, "1013", "22")
    assert row["HIGHSD"] == "Y"
    assert row["複核狀態"] == "請人工複核"
    assert result.manual_review_count == 1


def test_highsd_nd_is_treated_as_expected_not_review(study_dir, config):
    result = run_pipeline(config)
    row = _row(result.consolidated, "1012", "03")
    assert row["HIGHSD"] == "Y"
    assert row["呈現結果(低於LLOQ標示ND)"] == "ND"
    assert "預期現象" in row["複核狀態"]


def test_pipeline_never_writes_to_raw_files(study_dir, config):
    raw_dir = study_dir / "data/raw"
    before = {p.name: p.read_bytes() for p in raw_dir.glob("*.xls")}
    run_pipeline(config)
    after = {p.name: p.read_bytes() for p in raw_dir.glob("*.xls")}
    assert before == after, "原始檔是唯一真實來源，pipeline 絕不能改動它"


def test_manifest_records_input_hashes(study_dir, config):
    result = run_pipeline(config)
    hashes = [i["SHA-256"] for i in result.manifest.inputs if i.get("SHA-256")]
    assert len(hashes) == 2
    assert all(len(h) == 64 for h in hashes)


def test_rerunning_is_deterministic(study_dir, config):
    first = run_pipeline(config).consolidated
    second = run_pipeline(config).consolidated
    pd.testing.assert_frame_equal(first, second)


# --- 決策表：同仁實際的參與方式 -------------------------------------------

def _write_decision(path: Path, sheet: str, row: dict) -> None:
    sheets = pd.read_excel(path, sheet_name=None, dtype=object)
    frame = sheets[sheet]
    sheets[sheet] = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, data in sheets.items():
            data.to_excel(writer, sheet_name=name, index=False)


def test_highsd_decision_is_applied_and_audited(study_dir, config):
    decisions_path = create_template(config.path("decisions_file"))
    _write_decision(decisions_path, SHEET_HIGHSD, {
        "動物編號": "1013", "臟器代碼": "22",
        "來源檔案": "20260818_BD-TS-20260701_01_data.xls",
        "採用方式(孔位1/孔位2/兩孔平均)": "孔位1",
        "覆核者": "王小明", "覆核日期": "2026-09-05", "理由": "孔位2 擴增曲線異常",
    })

    result = run_pipeline(config)
    row = _row(result.consolidated, "1013", "22")

    assert row["複核狀態"].startswith("已人工複核：採用孔位1")
    assert "王小明" in row["複核狀態"]
    assert float(row["Quantity Mean 定量平均值"]) == pytest.approx(
        float(row["孔位1-Quantity"])
    )
    assert result.manual_review_count == 0

    audited = [d for d in result.manifest.decisions if d["決策類型"] == "HIGHSD單孔採用"]
    assert audited and audited[0]["覆核者"] == "王小明"


def test_decision_without_reviewer_is_rejected_not_silently_applied(study_dir, config):
    decisions_path = create_template(config.path("decisions_file"))
    _write_decision(decisions_path, SHEET_HIGHSD, {
        "動物編號": "1013", "臟器代碼": "22",
        "來源檔案": "20260818_BD-TS-20260701_01_data.xls",
        "採用方式(孔位1/孔位2/兩孔平均)": "孔位1",
        "覆核者": "", "覆核日期": "", "理由": "忘了填覆核者",
    })

    result = run_pipeline(config)
    assert result.manual_review_count == 1, "缺覆核者的決策不得生效"
    assert any("缺少覆核者或覆核日期" in w for w in result.warnings)


def test_final_use_decision_overrides_default_rerun_rule(study_dir, config):
    decisions_path = create_template(config.path("decisions_file"))
    _write_decision(decisions_path, SHEET_FINAL, {
        "動物編號": "1011", "臟器代碼": "03",
        "來源檔案": "20260818_BD-TS-20260701_01_data.xls",
        "最終採用(Y/N)": "Y",
        "覆核者": "李研究員", "覆核日期": "2026-09-05", "理由": "rerun 上樣量有疑慮",
    })

    result = run_pipeline(config)
    row = _row(result.consolidated, "1011", "03")
    assert row["版本"] == "原始"
    assert row["採用依據"] == "決策表指定"
    assert "李研究員" in row["備註"]


def test_leading_zeros_survive_excel_round_trip(study_dir, config):
    """Excel 常把 '0006' 存成數字 6；決策表必須還原得回來。"""
    decisions_path = create_template(config.path("decisions_file"))
    _write_decision(decisions_path, SHEET_GROUP, {
        "動物編號": 6, "組別": "Group 1 (Control)",
        "覆核者": "李研究員", "覆核日期": "2026-09-05", "理由": "測試前導零",
    })
    loaded = load_decisions(decisions_path)
    assert "0006" in loaded.group_override


def test_unknown_organ_code_is_reported_not_guessed(study_dir, config):
    raw = study_dir / "data/raw/20260818_BD-TS-20260701_01_data.xls"
    text = raw.read_text(encoding="utf-8").replace("1006_99", "1006_77")
    raw.write_text(text, encoding="utf-8")

    result = run_pipeline(config)
    assert any("臟器代碼 77" in w for w in result.warnings)
    codes = _read(result.output_path, "臟器代碼對照表")
    unknown = codes[codes["代碼"].astype(str).str.zfill(2) == "77"]
    assert len(unknown) == 1
    assert unknown.iloc[0]["資料來源"] == "尚未收錄"
