"""把 pipeline 產出與既有統整表逐列對帳。

這是導入時最重要的一步：不是「跑得動」就算過，而是要證明新流程能重現你已經
人工確認過的結果。有差異不一定是 pipeline 錯 —— 也可能是舊表當初的人工調整
沒有記錄下來。重點是把差異一筆筆攤開來看，而不是整份眼睛掃過去。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

# 對帳用的鍵：一筆結果通常由「哪一隻動物的哪個臟器、來自哪個原始檔」唯一決定。
# 但同一塊盤上可能同時有 1011_03 與 1011_03_re 兩個版本，這時三欄會撞號，
# 需要把 Sample Name 一起納入 —— compare_tables 偵測到撞號時會自動升級。
KEY_COLUMNS = ["動物編號", "臟器代碼", "來源檔案"]
VERSION_COLUMN = "Sample Name"

# 需要逐欄比對的欄位。數值欄用容差比較，其餘用字串比較。
NUMERIC_COLUMNS = [
    "Quantity Mean 定量平均值",
    "Ct Mean",
    "LLOQ(該run標準曲線最後一點,pg)",
    "孔位1-Ct",
    "孔位1-Quantity",
    "孔位2-Ct",
    "孔位2-Quantity",
]
TEXT_COLUMNS = [
    "版本",
    "最終採用",
    "HIGHSD",
    "呈現結果(低於LLOQ標示ND)",
    "性別(Sex)",
    "組別(Group)",
    "採樣時間點(Time point)",
    "孔位1-Well",
    "孔位2-Well",
]

DEFAULT_SHEET = "統整數據"


@dataclass
class ComparisonResult:
    matched: int = 0
    only_in_old: pd.DataFrame = field(default_factory=pd.DataFrame)
    only_in_new: pd.DataFrame = field(default_factory=pd.DataFrame)
    differences: pd.DataFrame = field(default_factory=pd.DataFrame)
    skipped_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def values_match(self) -> bool:
        """兩邊都有的列，值是否完全一致。"""
        return self.differences.empty

    @property
    def coverage_matches(self) -> bool:
        """兩邊涵蓋的列是否相同。分階段驗收時常常只跑部分 run。"""
        return self.only_in_old.empty and self.only_in_new.empty

    @property
    def is_clean(self) -> bool:
        return self.values_match and self.coverage_matches

    @property
    def differing_rows(self) -> int:
        if self.differences.empty:
            return 0
        return int(self.differences["對照鍵"].nunique())


def _norm_id(value: Any, width: int) -> str:
    """還原識別碼的前導 0。舊表存成數字 6 時要能對上新表的 '0006'。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if text.isdigit() and len(text) < width:
        return text.zfill(width)
    return text


def _norm_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _as_number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _prepare(frame: pd.DataFrame, with_version: bool) -> pd.DataFrame:
    prepared = frame.copy()
    prepared["動物編號"] = prepared["動物編號"].map(lambda v: _norm_id(v, 4))
    prepared["臟器代碼"] = prepared["臟器代碼"].map(lambda v: _norm_id(v, 2))
    prepared["來源檔案"] = prepared["來源檔案"].map(_norm_text)

    if with_version and VERSION_COLUMN in prepared.columns:
        label = prepared[VERSION_COLUMN].map(_norm_text)
        # Sample Name 空白時退回 動物_臟器，才不會整批變成同一個鍵
        label = label.where(
            label != "", prepared["動物編號"] + "_" + prepared["臟器代碼"]
        )
    else:
        label = prepared["動物編號"] + "_" + prepared["臟器代碼"]

    prepared["對照鍵"] = label + " @ " + prepared["來源檔案"]
    return prepared


def _has_duplicate_keys(frame: pd.DataFrame) -> bool:
    return bool(frame["對照鍵"].duplicated().any())


def load_previous(path: str | Path, sheet: str = DEFAULT_SHEET) -> pd.DataFrame:
    """讀取既有統整表的統整數據分頁。"""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"找不到要對照的統整表：{target}")

    available = pd.ExcelFile(target).sheet_names
    if sheet not in available:
        raise ValueError(
            f"統整表 {target.name} 裡找不到分頁「{sheet}」。"
            f"現有分頁：{', '.join(available)}"
        )

    frame = pd.read_excel(target, sheet_name=sheet, dtype=object)
    missing = [c for c in KEY_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(
            f"統整表分頁「{sheet}」缺少對照所需的欄位：{', '.join(missing)}。"
            "請確認指定的是正確的分頁。"
        )
    return frame


def compare_tables(old: pd.DataFrame, new: pd.DataFrame, *,
                   tolerance: float = 1e-6) -> ComparisonResult:
    """逐列逐欄比對兩份統整數據。

    數值以相對容差比較（預設 1e-6），吸收 Excel 儲存與浮點運算的尾差，
    但仍抓得出任何有意義的計算差異。
    """
    result = ComparisonResult()

    # 先用三欄鍵；任一邊撞號就把 Sample Name 納入，讓同盤的多版本分得開
    old_prepared = _prepare(old, with_version=False)
    new_prepared = _prepare(new, with_version=False)
    if _has_duplicate_keys(old_prepared) or _has_duplicate_keys(new_prepared):
        old_prepared = _prepare(old, with_version=True)
        new_prepared = _prepare(new, with_version=True)
        result.notes.append(
            "偵測到同一動物＋臟器＋來源檔案有多個版本（例如同盤的原始與 _re），"
            f"已自動改用「{VERSION_COLUMN}」作為對照鍵。若兩邊的 "
            f"{VERSION_COLUMN} 寫法不同，會顯示為「只在某一邊」的列，請留意。"
        )

    old_keys = set(old_prepared["對照鍵"])
    new_keys = set(new_prepared["對照鍵"])

    result.only_in_old = old_prepared[
        ~old_prepared["對照鍵"].isin(new_keys)
    ][["對照鍵"] + KEY_COLUMNS].reset_index(drop=True)
    result.only_in_new = new_prepared[
        ~new_prepared["對照鍵"].isin(old_keys)
    ][["對照鍵"] + KEY_COLUMNS].reset_index(drop=True)

    shared = sorted(old_keys & new_keys)
    result.matched = len(shared)

    numeric = [c for c in NUMERIC_COLUMNS if c in old.columns and c in new.columns]
    text = [c for c in TEXT_COLUMNS if c in old.columns and c in new.columns]
    result.skipped_columns = [
        c for c in NUMERIC_COLUMNS + TEXT_COLUMNS
        if c not in numeric and c not in text
    ]

    old_indexed = old_prepared.set_index("對照鍵")
    new_indexed = new_prepared.set_index("對照鍵")

    # 升級鍵值後仍然重複，代表資料本身有問題，攤出來
    for label, frame in (("既有統整表", old_prepared), ("pipeline 產出", new_prepared)):
        duplicated = frame["對照鍵"].duplicated(keep=False)
        if duplicated.any():
            keys = sorted(set(frame.loc[duplicated, "對照鍵"]))
            result.notes.append(
                f"{label}有 {len(keys)} 個重複的對照鍵（同一動物＋臟器＋來源檔案出現多列），"
                f"對照時只取第一列：{'、'.join(keys[:5])}"
                + ("…" if len(keys) > 5 else "")
            )

    old_indexed = old_indexed[~old_indexed.index.duplicated(keep="first")]
    new_indexed = new_indexed[~new_indexed.index.duplicated(keep="first")]

    rows: list[dict[str, Any]] = []
    for key in shared:
        old_row = old_indexed.loc[key]
        new_row = new_indexed.loc[key]

        for column in numeric:
            old_value = _as_number(old_row.get(column))
            new_value = _as_number(new_row.get(column))
            if _numbers_match(old_value, new_value, tolerance):
                continue
            rows.append({
                "對照鍵": key,
                "欄位": column,
                "既有統整表": old_row.get(column),
                "pipeline產出": new_row.get(column),
                "差異": _describe_delta(old_value, new_value),
            })

        for column in text:
            old_value = _norm_text(old_row.get(column))
            new_value = _norm_text(new_row.get(column))
            if old_value == new_value:
                continue
            rows.append({
                "對照鍵": key,
                "欄位": column,
                "既有統整表": old_value or "(空白)",
                "pipeline產出": new_value or "(空白)",
                "差異": "文字不一致",
            })

    result.differences = pd.DataFrame(rows)
    return result


def _numbers_match(old: float | None, new: float | None, tolerance: float) -> bool:
    if old is None and new is None:
        return True
    if old is None or new is None:
        return False
    scale = max(abs(old), abs(new), 1e-12)
    return abs(old - new) / scale <= tolerance


def _describe_delta(old: float | None, new: float | None) -> str:
    if old is None:
        return "既有表無數值"
    if new is None:
        return "pipeline 無數值"
    delta = new - old
    scale = max(abs(old), 1e-12)
    return f"{delta:+.6g}（相對 {delta / scale:+.3%}）"


def write_comparison_report(path: str | Path, result: ComparisonResult) -> Path:
    """把對照結果寫成一份 xlsx，方便逐筆討論。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    summary = pd.DataFrame([
        ("兩邊都有的列數", result.matched),
        ("有差異的列數", result.differing_rows),
        ("差異欄位總數", len(result.differences)),
        ("只在既有統整表中的列數", len(result.only_in_old)),
        ("只在 pipeline 產出中的列數", len(result.only_in_new)),
        ("重疊列的值", "完全一致" if result.values_match else "有差異，請逐筆確認"),
        ("涵蓋範圍", "相同" if result.coverage_matches else "不同（可能只跑了部分原始檔）"),
    ], columns=["項目", "內容"])

    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="對照摘要", index=False)
        _sheet(writer, "逐欄差異", result.differences, "兩邊數值一致，沒有差異。")
        _sheet(writer, "只在既有表", result.only_in_old, "沒有這類列。")
        _sheet(writer, "只在pipeline", result.only_in_new, "沒有這類列。")
        notes = result.notes + [f"未比對的欄位（兩邊至少一方沒有）：{c}"
                                for c in result.skipped_columns]
        _sheet(writer, "備註", pd.DataFrame({"訊息": notes}), "無備註。")

    return target


def _sheet(writer: pd.ExcelWriter, name: str, frame: pd.DataFrame,
           empty_message: str) -> None:
    if frame is None or frame.empty:
        frame = pd.DataFrame({"結果": [empty_message]})
    frame.to_excel(writer, sheet_name=name, index=False)
