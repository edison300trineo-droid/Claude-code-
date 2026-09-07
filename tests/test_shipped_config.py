"""隨專案出貨的設定檔本身要是有效的。

設定檔會為了部署而改（例如把路徑指向 NAS），改壞了要當場知道，
而不是等到執行時才發現。UNC 路徑的 YAML 引號尤其容易踩。
"""

from pathlib import Path

import pytest
import yaml

from qpcr_biod.config import load_config

CONFIG = Path(__file__).resolve().parents[1] / "config" / "BD-TS-20260701.yaml"


def test_shipped_config_is_valid_yaml():
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)


def test_shipped_config_loads():
    config = load_config(CONFIG)
    assert config.study_id == "BD-TS-20260701"


def test_every_path_is_a_non_empty_string():
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    for key in ("raw_dir", "reference_dir", "decisions_file", "output_dir"):
        value = raw["paths"].get(key)
        assert isinstance(value, str) and value.strip(), f"paths.{key} 未設定"


def test_backslash_paths_survive_yaml_quoting():
    """YAML 雙引號會把 \\ 當跳脫字元；UNC 路徑必須用單引號。

    寫錯的話輕則路徑被竄改，重則整份設定解析失敗。
    """
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    for key, value in raw["paths"].items():
        if not value.startswith("\\\\"):
            continue  # 非 UNC 路徑不受影響
        assert value.count("\\\\") == 1 and value.startswith("\\\\"), (
            f"paths.{key} 的反斜線被 YAML 竄改了：{value!r}\n"
            "UNC 路徑請改用單引號包起來。"
        )
        assert "\\" in value[2:], f"paths.{key} 看起來不是完整的 UNC 路徑：{value!r}"


def test_standard_series_is_the_confirmed_five_fold_dilution():
    config = load_config(CONFIG)
    series = config.standard_curve["nominal_concentrations"]
    assert list(series.values()) == [10000, 2000, 400, 80, 16, 3.2, 0.64, 0.128]


def test_acceptance_criteria_match_the_protocol():
    config = load_config(CONFIG)
    assert config.standard_curve["min_r_squared"] == 0.98
    assert config.standard_curve["efficiency_range"] == [85.0, 115.0]
    assert config.qc["sensitivity_control"]["recovery_range"] == [80.0, 120.0]


def test_all_sixteen_organ_codes_are_present():
    config = load_config(CONFIG)
    assert len(config.organ_codes) == 16
    assert config.organ_name("27", "en") == "Blood"


def test_no_organ_code_belongs_to_two_batches():
    """一個臟器只能屬於一個批次，否則 Run 的批次判定會有歧義。"""
    config = load_config(CONFIG)
    assigned = [code for codes in config.raw["batches"].values() for code in codes]
    duplicated = {code for code in assigned if assigned.count(code) > 1}
    assert not duplicated, f"這些臟器代碼被分到多個批次：{sorted(duplicated)}"


def test_every_batched_code_exists_in_the_organ_table():
    """批次不能引用一個對照表裡沒有的代碼。"""
    config = load_config(CONFIG)
    assigned = {code for codes in config.raw["batches"].values() for code in codes}
    unknown = assigned - set(config.organ_codes)
    assert not unknown, f"批次引用了未收錄的臟器代碼：{sorted(unknown)}"


def test_ovaries_share_the_testis_slot_rather_than_having_their_own_batch():
    """卵巢(11)不自成批次，而是與睪丸(25)共用同一個上機位置。

    官方對照表只寫得下一個代碼，所以互換關係設在 organ_code_alternatives，
    這樣不論批次組成來自官方表還是設定檔都會生效。
    """
    config = load_config(CONFIG)
    assigned = {code for codes in config.raw["batches"].values() for code in codes}
    assert "11" not in assigned, "卵巢不該自成一個批次位置"

    alternatives = config.raw.get("organ_code_alternatives") or {}
    assert alternatives.get("25") == ["11"], (
        "睪丸(25)與卵巢(11)的互換關係未設定，"
        "卵巢檢體會被判為不屬於任何批次。"
    )


def test_every_alternative_code_exists_in_the_organ_table():
    config = load_config(CONFIG)
    alternatives = config.raw.get("organ_code_alternatives") or {}
    for primary, swaps in alternatives.items():
        assert primary in config.organ_codes, f"互換設定的主要代碼 {primary} 未收錄"
        for code in swaps:
            assert code in config.organ_codes, f"互換設定的代碼 {code} 未收錄"
