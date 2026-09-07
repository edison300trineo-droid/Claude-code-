"""StepOnePlus export (.xls) 讀取。

儀器匯出的 .xls 有兩種實體格式，這裡都要吃得下：

* 真正的 BIFF .xls（xlrd 可讀）
* 副檔名叫 .xls、內容其實是 tab 分隔純文字（StepOnePlus 常見）

兩種都帶一段 preamble（Experiment File Name / Experiment Run End Time …），
真正的表頭是第一列同時出現 "Well" 與 "Sample Name" 的那一列。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

# StepOnePlus 的表頭把 Ct 寫成 "Cт" —— 那個 т 是西里爾字母 U+0442，不是拉丁 t。
# 逐一猜變體會漏（"Cт Mean"、"Cт SD" 都中招），所以比對前先做同形字正規化。
HOMOGLYPHS = str.maketrans({
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
    "\u0442": "t", "\u0445": "x", "\u0443": "y", "\u0456": "i", "\u0458": "j",
    "\u0410": "A", "\u0415": "E", "\u041e": "O", "\u0420": "P", "\u0421": "C",
    "\u0422": "T", "\u0425": "X", "\u041c": "M", "\u041d": "H", "\u041a": "K",
    "\u0412": "B", "\u0406": "I",
})


def normalize_header(name: str) -> str:
    """把表頭正規化成可比對的鍵：同形字轉拉丁、收斂空白、轉小寫。"""
    return " ".join(str(name).translate(HOMOGLYPHS).split()).lower()


# 儀器欄位 -> 內部欄位。缺的欄位一律補 None，不讓下游 KeyError。
COLUMN_ALIASES: dict[str, str] = {
    "well": "well",
    "well position": "well_position",
    "sample name": "sample_name",
    "target name": "target_name",
    "task": "task",
    "ct": "ct",
    "ct mean": "ct_mean",
    "ct sd": "ct_sd",
    "quantity": "quantity",
    "quantity mean": "quantity_mean",
    "quantity sd": "quantity_sd",
    "highsd": "highsd",
    "automatic ct threshold": "auto_ct_threshold",
    "ct threshold": "ct_threshold",
    "automatic baseline": "auto_baseline",
    "baseline start": "baseline_start",
    "baseline end": "baseline_end",
    "comments": "comments",
}

REQUIRED_COLUMNS = ("well", "sample_name", "task")

NUMERIC_COLUMNS = (
    "ct",
    "ct_mean",
    "ct_sd",
    "quantity",
    "quantity_mean",
    "quantity_sd",
)

# Ct 為 "Undetermined" 時儀器不給數值；統一轉成 NaN 而不是 0。
UNDETERMINED = {"undetermined", "undet", "n/a", "na", "", "-"}


class ParseError(ValueError):
    """原始檔案無法解析。"""


@dataclass
class RunFile:
    """一個原始 export 檔 = 一個 run。"""

    path: Path
    filename: str
    run_end_time: str | None
    preamble: dict[str, str]
    wells: pd.DataFrame
    sha256: str
    has_highsd_column: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def run_end_time_sortable(self) -> datetime:
        """排序用時間。無法解析時退回檔名，讓順序仍然穩定可預期。"""
        parsed = parse_run_end_time(self.run_end_time)
        if parsed is not None:
            return parsed
        return datetime.max


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_run_end_time(value: str | None) -> datetime | None:
    """解析 'Experiment Run End Time'。

    儀器格式例：'2026-08-18 15:00:52 PM CST'（注意 24 小時制後面還跟著 PM，
    這是儀器本身的怪癖，不是資料錯誤）。'Not Started' 等值回傳 None。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"not started", "n/a", "na", "-"}:
        return None
    # 去掉時區與 AM/PM 贅字後再解析
    tokens = text.split()
    cleaned: list[str] = []
    for token in tokens:
        if token.upper() in {"AM", "PM"}:
            continue
        if token.isalpha() and len(token) >= 2 and token.isupper():
            continue  # 時區縮寫 CST/UTC…
        cleaned.append(token)
    candidate = " ".join(cleaned)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def _read_rows(path: Path) -> list[list[str]]:
    """把檔案讀成純字串的二維列表，兩種實體格式都走這裡。"""
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        frame = pd.read_excel(path, header=None, dtype=str, engine="openpyxl")
        return _frame_to_rows(frame)

    # .xls：先試 BIFF，失敗再當作分隔文字
    if suffix == ".xls":
        try:
            frame = pd.read_excel(path, header=None, dtype=str, engine="xlrd")
            return _frame_to_rows(frame)
        except Exception:  # noqa: BLE001 - 退回文字解析是正常路徑，不是例外狀況
            pass

    return _read_delimited_text(path)


def _frame_to_rows(frame: pd.DataFrame) -> list[list[str]]:
    rows: list[list[str]] = []
    for record in frame.itertuples(index=False):
        rows.append(["" if pd.isna(v) else str(v).strip() for v in record])
    return rows


def _read_delimited_text(path: Path) -> list[list[str]]:
    raw: str | None = None
    for encoding in ("utf-8-sig", "utf-16", "cp950", "big5", "latin-1"):
        try:
            raw = path.read_text(encoding=encoding)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if raw is None:
        raise ParseError(f"無法判斷檔案編碼: {path}")

    delimiter = "\t" if "\t" in raw else ","
    rows: list[list[str]] = []
    for line in raw.splitlines():
        rows.append([cell.strip() for cell in line.split(delimiter)])
    return rows


def _find_header_index(rows: list[list[str]]) -> int:
    for idx, row in enumerate(rows):
        lowered = {normalize_header(cell) for cell in row if cell}
        if "well" in lowered and "sample name" in lowered:
            return idx
    raise ParseError("找不到表頭列（需同時含 'Well' 與 'Sample Name'）")


def _parse_preamble(rows: list[list[str]], header_index: int) -> dict[str, str]:
    """表頭之前的 key/value 區塊。"""
    preamble: dict[str, str] = {}
    for row in rows[:header_index]:
        cells = [c for c in row if c]
        if len(cells) >= 2:
            preamble.setdefault(cells[0].rstrip(":").strip(), cells[1])
    return preamble


def _to_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in UNDETERMINED:
        return None
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def read_run_file(path: str | Path) -> RunFile:
    """讀取單一 export 檔，回傳逐孔資料與檔案層級 metadata。"""
    p = Path(path)
    if not p.is_file():
        raise ParseError(f"找不到檔案: {p}")

    rows = _read_rows(p)
    if not rows:
        raise ParseError(f"檔案沒有內容: {p}")

    header_index = _find_header_index(rows)
    preamble = _parse_preamble(rows, header_index)
    header = [cell.strip() for cell in rows[header_index]]

    # 對應欄名；未知欄位保留原名（小寫底線化），不丟資料
    mapped: list[str] = []
    seen: set[str] = set()
    for raw_name in header:
        key = normalize_header(raw_name)
        name = COLUMN_ALIASES.get(key)
        if name is None:
            name = key.replace(" ", "_") if key else f"col_{len(mapped)}"
        # 同名欄位加序號，避免 DataFrame 欄名衝突後續 silently 取錯欄
        if name in seen:
            suffix = 2
            while f"{name}_{suffix}" in seen:
                suffix += 1
            name = f"{name}_{suffix}"
        seen.add(name)
        mapped.append(name)

    body: list[dict[str, Any]] = []
    width = len(mapped)
    for row in rows[header_index + 1 :]:
        if not any(cell for cell in row):
            continue
        padded = list(row[:width]) + [""] * max(0, width - len(row))
        record = dict(zip(mapped, padded))
        # 沒有 Well 的列是檔尾說明文字，不是資料
        if not str(record.get("well", "")).strip():
            continue
        body.append(record)

    if not body:
        raise ParseError(f"表頭之後沒有任何孔位資料: {p}")

    frame = pd.DataFrame(body)
    for column in REQUIRED_COLUMNS:
        if column not in frame.columns:
            raise ParseError(f"檔案缺少必要欄位 '{column}': {p}")

    for column in NUMERIC_COLUMNS:
        if column in frame.columns:
            frame[column] = frame[column].map(_to_number)
        else:
            frame[column] = None

    has_highsd = "highsd" in frame.columns
    if not has_highsd:
        # 儀器只在整盤有 HIGHSD=Y 時才輸出該欄位，欄位不存在代表整盤皆為 N。
        frame["highsd"] = None

    warnings: list[str] = []
    run_end_time = preamble.get("Experiment Run End Time")
    if not has_highsd:
        warnings.append(
            "此檔案未含 HIGHSD 欄位。儀器僅在整盤存在 HIGHSD=Y 的孔位時才會匯出該欄位，"
            "整盤皆為 N 時該欄位本就不會出現，屬正常現象、非資料遺漏。"
        )
    if parse_run_end_time(run_end_time) is None:
        warnings.append(
            f"「Experiment Run End Time」為 {run_end_time!r}，無法解析為時間。"
            "建議與原始儀器紀錄核對此檔是否為完整定案匯出檔。"
        )

    frame.insert(0, "source_file", p.name)
    frame.insert(1, "run_end_time", run_end_time)

    return RunFile(
        path=p,
        filename=p.name,
        run_end_time=run_end_time,
        preamble=preamble,
        wells=frame,
        sha256=sha256_of(p),
        has_highsd_column=has_highsd,
        warnings=warnings,
    )


def discover_run_files(raw_dir: str | Path, patterns: tuple[str, ...] = ("*.xls", "*.xlsx")) -> list[Path]:
    """找出資料夾中的原始 export 檔，排除 Excel 暫存檔。"""
    directory = Path(raw_dir)
    if not directory.is_dir():
        raise ParseError(f"找不到原始資料夾: {directory}")
    found: list[Path] = []
    for pattern in patterns:
        for path in directory.glob(pattern):
            if path.name.startswith("~$"):
                continue
            found.append(path)
    return sorted(set(found), key=lambda p: p.name)
