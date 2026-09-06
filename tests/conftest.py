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
    shutil.copy(ROOT / "config/BD-TS-20260701.yaml", tmp_path / "config/study.yaml")
    return tmp_path


@pytest.fixture
def config(study_dir: Path):
    from qpcr_biod.config import load_config
    return load_config(study_dir / "config/study.yaml")
