import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = Path(__file__).parent / "fixtures"

REFERENCE_ROWS = [
    ("0006", "Female", "Predose"), ("0007", "Female", "Predose"),
    ("0008", "Female", "Predose"), ("0009", "Female", "Predose"),
    ("0010", "Female", "Predose"), ("1006", "Male", "Predose"),
    ("1007", "Male", "Predose"), ("1008", "Male", "Predose"),
    ("1009", "Male", "Predose"), ("1010", "Male", "Predose"),
    ("1011", "Male", "Day 2"), ("1012", "Male", "Day 2"),
    ("1013", "Male", "Day 2"), ("1014", "Male", "Day 2"),
    ("1024", "Male", "Day 15"),
    ("1031", "Male", "Day 2"),
]


@pytest.fixture
def study_dir(tmp_path: Path) -> Path:
    """建立一個完整的研究資料夾：raw / reference / output / config。"""
    for sub in ("data/raw", "data/reference", "output", "config"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)

    for source in FIXTURES.glob("*.xls"):
        shutil.copy(source, tmp_path / "data/raw" / source.name)

    pd.DataFrame(REFERENCE_ROWS, columns=["Animal No", "Sex", "Time point"]).to_excel(
        tmp_path / "data/reference/animals.xlsx", index=False
    )
    _write_test_config(tmp_path / "config/study.yaml")
    return tmp_path


def _write_test_config(target: Path) -> None:
    """複製正式設定，但把 paths 指回這次測試的暫存資料夾。

    正式設定的 paths 指向部署用的 NAS 位置。測試要驗的是規則，不是部署位置 ——
    直接沿用會讓「改了部署路徑」變成「測試壞掉」，那是假訊號。
    """
    import yaml

    raw = yaml.safe_load((ROOT / "config/BD-TS-20260701.yaml").read_text(encoding="utf-8"))
    raw["paths"] = {
        "raw_dir": "../data/raw",
        "reference_dir": "../data/reference",
        "decisions_file": "../data/decisions.xlsx",
        "output_dir": "../output",
    }
    target.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


@pytest.fixture
def config(study_dir: Path):
    from qpcr_biod.config import load_config
    return load_config(study_dir / "config/study.yaml")
