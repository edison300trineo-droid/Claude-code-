from datetime import datetime
from pathlib import Path

import pytest

from qpcr_biod.parsing import (
    ParseError,
    discover_run_files,
    parse_run_end_time,
    read_run_file,
)

FIXTURES = Path(__file__).parent / "fixtures"
RUN01 = FIXTURES / "20260818_BD-TS-20260701_01_data.xls"
RUN02 = FIXTURES / "20260824_BD-TS-20260701_07_data.xls"


def test_reads_preamble_and_wells():
    run = read_run_file(RUN01)
    assert run.preamble["Instrument Type"] == "steponeplus"
    assert run.run_end_time == "2026-08-18 15:00:52 PM CST"
    assert run.has_highsd_column is True
    # 16 標準品 + 28 檢體(14×2) + 6 敏感度 + 2 基質 + 2 NTC
    assert len(run.wells) == 54
    assert set(["source_file", "well", "sample_name", "task", "ct"]).issubset(run.wells.columns)


def test_numeric_columns_are_numeric_and_undetermined_is_nan():
    run = read_run_file(RUN02)
    ntc = run.wells[run.wells["sample_name"] == "NTC"]
    assert len(ntc) == 2
    # Undetermined 必須是 NaN，不能變成 0 —— 0 會被誤判成偵測到訊號
    assert ntc["ct"].isna().all()
    std01 = run.wells[run.wells["sample_name"] == "STD01"]
    assert std01["quantity"].iloc[0] == pytest.approx(10000.0)


def test_missing_highsd_column_is_flagged_not_fatal():
    run = read_run_file(RUN02)
    assert run.has_highsd_column is False
    assert "highsd" in run.wells.columns
    assert run.wells["highsd"].isna().all()
    assert any("HIGHSD" in w for w in run.warnings)


def test_run_end_time_parsing_handles_instrument_quirks():
    # 儀器同時輸出 24 小時制與 PM，且帶時區縮寫
    assert parse_run_end_time("2026-08-18 15:00:52 PM CST") == datetime(2026, 8, 18, 15, 0, 52)
    assert parse_run_end_time("Not Started") is None
    assert parse_run_end_time(None) is None


def test_not_started_run_produces_warning():
    run = read_run_file(RUN01)
    assert run.run_end_time_sortable == datetime(2026, 8, 18, 15, 0, 52)


def test_sha256_is_stable():
    assert read_run_file(RUN01).sha256 == read_run_file(RUN01).sha256


def test_discover_skips_excel_lock_files(tmp_path):
    (tmp_path / "~$real.xls").write_text("lock", encoding="utf-8")
    (tmp_path / "real.xls").write_text("x", encoding="utf-8")
    found = discover_run_files(tmp_path)
    assert [p.name for p in found] == ["real.xls"]


def test_missing_header_raises():
    bad = FIXTURES / "_bad.xls"
    bad.write_text("no header here\njust text\n", encoding="utf-8")
    try:
        with pytest.raises(ParseError):
            read_run_file(bad)
    finally:
        bad.unlink()
