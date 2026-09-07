"""批次比對與 Run 進度推定。

重點不只是「能不能對到批次」，更是「推定出來的東西有沒有被標示成推定」——
官方排定與逆推結果混在一起卻看不出差別，比沒有這張表更糟。
"""

import pandas as pd
import pytest

from qpcr_biod.batches import SOURCE_INFERRED, SOURCE_OFFICIAL, build_batch_tables
from qpcr_biod.classify import SampleClass


def _wells(spec: dict[str, list[str]]) -> pd.DataFrame:
    """spec: {來源檔案: [臟器代碼, ...]}"""
    rows = []
    for source_file, codes in spec.items():
        for code in codes:
            rows.append({
                "source_file": source_file, "sample_class": SampleClass.ANIMAL.value,
                "organ_code": code, "animal_id": "1006",
            })
    return pd.DataFrame(rows)


def _consolidated(spec: dict[str, str]) -> pd.DataFrame:
    """spec: {來源檔案: 採樣時間點}"""
    return pd.DataFrame([
        {"來源檔案": source_file, "採樣時間點(Time point)": timepoint}
        for source_file, timepoint in spec.items()
    ])


def _progress(config, wells_spec, consolidated_spec):
    order = {name: index for index, name in enumerate(wells_spec)}
    tables = build_batch_tables(_wells(wells_spec), _consolidated(consolidated_spec),
                                order, config)
    return tables


def test_composition_lists_every_configured_batch(config):
    tables = _progress(config, {"run01.xls": ["99", "03", "64"]},
                       {"run01.xls": "Predose"})
    assert len(tables.composition) == 5
    first = tables.composition.iloc[0]
    assert first["批次(Batch)"] == "99+03+64"
    assert "Injection site" in first["臟器名稱1"]


def test_complete_batch_at_a_scheduled_timepoint_is_marked_official(config):
    tables = _progress(config, {"run01.xls": ["99", "03", "64"]},
                       {"run01.xls": "Predose"})
    row = tables.progress.iloc[0]

    assert row["批次(Batch)"] == "99+03+64"
    assert row["批次比對"] == "完全相符"
    assert row["官方已排定Run編號"] == 1
    assert row["批次/時間點資料來源"] == SOURCE_OFFICIAL


def test_batch_at_an_unscheduled_timepoint_is_marked_inferred(config):
    """Day 15 不在官方排定的 6 組內，必須標示為逆推。"""
    tables = _progress(config, {"run01.xls": ["99", "03", "64"]},
                       {"run01.xls": "Day 15"})
    row = tables.progress.iloc[0]

    assert row["批次(Batch)"] == "99+03+64"
    assert row["官方已排定Run編號"] == ""
    assert row["批次/時間點資料來源"] == SOURCE_INFERRED


def test_partial_batch_still_matches_but_says_what_is_missing(config):
    """某臟器該次沒送測時仍要對得到批次，但不能假裝是完整的。"""
    tables = _progress(config, {"run01.xls": ["99", "03"]},
                       {"run01.xls": "Predose"})
    row = tables.progress.iloc[0]

    assert row["批次(Batch)"] == "99+03+64"
    assert "部分相符" in row["批次比對"]
    assert "64" in row["批次比對"]


def test_organs_spanning_two_batches_are_reported_not_forced(config):
    tables = _progress(config, {"run01.xls": ["99", "27"]}, {"run01.xls": "Predose"})
    row = tables.progress.iloc[0]

    assert row["批次(Batch)"] == "(無法對應)"
    assert any("不屬於官方排程的任一批次" in note for note in tables.notes)
    # 常態現象不該被寫成待辦事項
    assert not any("請人工確認" in note for note in tables.notes)


def test_multiple_timepoints_in_one_file_are_flagged(config):
    wells = _wells({"run01.xls": ["99", "03", "64"]})
    consolidated = pd.DataFrame([
        {"來源檔案": "run01.xls", "採樣時間點(Time point)": "Predose"},
        {"來源檔案": "run01.xls", "採樣時間點(Time point)": "Day 2"},
    ])
    tables = build_batch_tables(wells, consolidated, {"run01.xls": 0}, config)

    assert any("多個採樣時間點" in note for note in tables.notes)
    assert any("屬正常" in note for note in tables.notes)
    assert tables.progress.iloc[0]["批次/時間點資料來源"] == SOURCE_INFERRED


def test_run_numbers_follow_run_order(config):
    tables = _progress(
        config,
        {"run01.xls": ["99", "03", "64"], "run02.xls": ["27", "23", "22"]},
        {"run01.xls": "Predose", "run02.xls": "Predose"},
    )
    assert list(tables.progress["Run編號(推定)"]) == [1, 2]
    assert list(tables.progress["官方已排定Run編號"]) == [1, 4]


def test_inference_is_always_disclosed(config):
    tables = _progress(config, {"run01.xls": ["99", "03", "64"]},
                       {"run01.xls": "Predose"})
    assert any("Run 編號為推定值" in note for note in tables.notes)
