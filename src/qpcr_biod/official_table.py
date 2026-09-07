"""官方「上機編號_說明」對照表解析。

這份檔案是自由格式的工作表，同一張紙上並排了三個互不相干的區塊：

    C–E 欄   採樣時間點，以及分成 M / F 兩欄的動物編號
    G–I 欄   組織代號、臟器名稱（英文 / 中文）
    K–P 欄   批次 × 時間點 的 Run 編號矩陣

所以性別不是一個欄位，而是「動物編號寫在哪一欄」—— 用欄名比對永遠抓不到。

解析策略是錨定標題文字（「動物編號」「組織代號」等）再往下讀，而不是寫死儲存格
位置，這樣多插一列或平移幾欄都還能讀。錨點找不到時就明說找不到，不硬猜。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BATCH_PATTERN = re.compile(r"^\d{2}(?:\+\d{2})+$")
ANIMAL_PATTERN = re.compile(r"^\d{4}$")
ORGAN_CODE_PATTERN = re.compile(r"^\d{2}$")

LABEL_ANIMAL = "動物編號"
LABEL_ORGAN_CODE = "組織代號"
LABEL_MALE = "M"
LABEL_FEMALE = "F"

SEX_MALE = "Male"
SEX_FEMALE = "Female"


@dataclass
class OfficialTable:
    animals: dict[str, dict[str, str]] = field(default_factory=dict)
    organ_codes: dict[str, dict[str, str]] = field(default_factory=dict)
    batches: dict[str, list[str]] = field(default_factory=dict)
    run_schedule: list[dict[str, Any]] = field(default_factory=list)
    unscheduled_sections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.animals or self.organ_codes or self.batches)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _animal_id(value: Any) -> str:
    """動物編號保留 4 碼前導零。Excel 會把 0006 存成數字 6。"""
    text = _text(value)
    if text.isdigit() and len(text) < 4:
        return text.zfill(4)
    return text


def _organ_code(value: Any) -> str:
    text = _text(value)
    if text.isdigit() and len(text) < 2:
        return text.zfill(2)
    return text


def read_grid(path: str | Path, sheet: str | None = None) -> list[list[str]]:
    """把工作表讀成純字串網格。"""
    from openpyxl import load_workbook

    workbook = load_workbook(Path(path), data_only=True)
    worksheet = workbook[sheet] if sheet else workbook.worksheets[0]
    grid: list[list[str]] = []
    for row in worksheet.iter_rows(values_only=True):
        grid.append([_text(cell) for cell in row])
    return grid


def _find_all(grid: list[list[str]], label: str) -> list[tuple[int, int]]:
    return [
        (r, c)
        for r, row in enumerate(grid)
        for c, value in enumerate(row)
        if value == label
    ]


def parse_official_table(path: str | Path, sheet: str | None = None) -> OfficialTable:
    """解析官方對照表，回傳四個區塊的內容。"""
    table = OfficialTable()
    grid = read_grid(path, sheet)
    if not grid:
        table.warnings.append(f"官方對照表 {Path(path).name} 沒有內容。")
        return table

    _parse_animals(grid, table)
    _parse_organs(grid, table)
    _parse_run_matrix(grid, table)
    return table


# --- 區塊一：動物編號 × 性別 × 時間點 ---------------------------------------

def _parse_animals(grid: list[list[str]], table: OfficialTable) -> None:
    anchors = _find_all(grid, LABEL_ANIMAL)
    if not anchors:
        table.warnings.append(
            f"官方對照表找不到「{LABEL_ANIMAL}」標題，未取得動物性別與時間點對照。"
        )
        return

    header_row = anchors[0][0]
    # 標題底下那一列標著 M / F，決定哪一欄是公、哪一欄是母
    sex_columns = _locate_sex_columns(grid, header_row, [c for _, c in anchors])
    if not sex_columns:
        table.warnings.append(
            f"官方對照表在「{LABEL_ANIMAL}」下方找不到 M / F 欄位標記，"
            "無法判定性別，未取得動物對照。"
        )
        return

    timepoint_column = min(sex_columns) - 1
    current_timepoint = ""

    for row_index in range(header_row + 1, len(grid)):
        row = grid[row_index]
        candidate = _cell(row, timepoint_column)
        # 時間點只寫在每組的第一列，其餘沿用上一個值
        if candidate and not ANIMAL_PATTERN.match(candidate):
            current_timepoint = candidate

        for column, sex in sex_columns.items():
            animal = _animal_id(_cell(row, column))
            if not ANIMAL_PATTERN.match(animal):
                continue
            existing = table.animals.get(animal)
            if existing and existing != {"sex": sex, "timepoint": current_timepoint}:
                table.warnings.append(
                    f"官方對照表中動物 {animal} 出現兩次且內容不一致："
                    f"{existing} vs {{'sex': {sex!r}, 'timepoint': {current_timepoint!r}}}"
                )
                continue
            table.animals[animal] = {"sex": sex, "timepoint": current_timepoint}


def _locate_sex_columns(grid: list[list[str]], header_row: int,
                        animal_columns: list[int]) -> dict[int, str]:
    """在標題列下方幾列內找 M / F 標記，回傳 {欄索引: 性別}。"""
    wanted = {LABEL_MALE: SEX_MALE, LABEL_FEMALE: SEX_FEMALE}
    span = range(min(animal_columns) - 1, max(animal_columns) + 2)

    for offset in range(1, 4):
        row_index = header_row + offset
        if row_index >= len(grid):
            break
        found = {
            column: wanted[_cell(grid[row_index], column)]
            for column in span
            if _cell(grid[row_index], column) in wanted
        }
        if len(found) == 2:
            return found
    return {}


def _cell(row: list[str], column: int) -> str:
    if column < 0 or column >= len(row):
        return ""
    return row[column]


# --- 區塊二：組織代號 × 臟器名稱 --------------------------------------------

def _parse_organs(grid: list[list[str]], table: OfficialTable) -> None:
    anchors = _find_all(grid, LABEL_ORGAN_CODE)
    if not anchors:
        table.warnings.append(
            f"官方對照表找不到「{LABEL_ORGAN_CODE}」標題，未取得臟器代碼對照。"
        )
        return

    header_row, code_column = anchors[0]
    blanks = 0
    for row_index in range(header_row + 1, len(grid)):
        row = grid[row_index]
        code = _organ_code(_cell(row, code_column))
        if not ORGAN_CODE_PATTERN.match(code):
            blanks += 1
            if blanks >= 3:
                break
            continue
        blanks = 0
        table.organ_codes[code] = {
            "en": _cell(row, code_column + 1),
            "zh": _cell(row, code_column + 2),
        }


# --- 區塊三：批次 × 時間點 的 Run 編號矩陣 ----------------------------------

def _parse_run_matrix(grid: list[list[str]], table: OfficialTable) -> None:
    batch_cells = [
        (r, c)
        for r, row in enumerate(grid)
        for c, value in enumerate(row)
        if BATCH_PATTERN.match(value)
    ]
    if not batch_cells:
        table.warnings.append("官方對照表找不到批次（例如 99+03+64），未取得上機排程。")
        return

    for row_index, column in batch_cells:
        name = grid[row_index][column]
        table.batches.setdefault(name, [f"{code:0>2}" for code in name.split("+")])

    # 同一欄可能疊了多個矩陣（例如主排程之下另有一塊 Day 29(Vehicle)），
    # 所以先把批次列切成連續群組，每一群各自往上找自己的表頭。
    for column in sorted({c for _, c in batch_cells}):
        rows = sorted(r for r, c in batch_cells if c == column)
        for group in _contiguous_groups(rows):
            _parse_matrix_block(grid, table, column, group)


def _contiguous_groups(rows: list[int], max_gap: int = 1) -> list[list[int]]:
    """把列索引切成連續群組，中間空超過 max_gap 列就視為不同區塊。"""
    groups: list[list[int]] = []
    for row in rows:
        if groups and row - groups[-1][-1] <= max_gap + 1:
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def _parse_matrix_block(grid: list[list[str]], table: OfficialTable,
                        column: int, rows: list[int]) -> None:
    header_row = _find_matrix_header(grid, rows[0], column)
    if header_row is None:
        table.warnings.append(
            f"批次欄（第 {column + 1} 欄，第 {rows[0] + 1} 列起）上方找不到表頭，"
            "該區塊的 Run 編號未解析。"
        )
        return

    headers = {
        c: grid[header_row][c]
        for c in range(column + 1, len(grid[header_row]))
        if grid[header_row][c]
    }
    section = _section_label(grid, header_row, column)
    assigned = False

    for row_index in rows:
        batch = grid[row_index][column]
        for c, timepoint in headers.items():
            run = _cell(grid[row_index], c)
            if not run.isdigit():
                continue
            assigned = True
            table.run_schedule.append({
                "run": int(run),
                "batch": batch,
                "timepoint": timepoint,
                "section": section,
            })

    if not assigned:
        label = section or f"第 {header_row + 1} 列的區塊"
        table.unscheduled_sections.append(label)
        table.warnings.append(
            f"官方對照表的「{label}」區塊列出了批次但沒有填 Run 編號，"
            "代表尚未排定上機。這些 Run 只能由實際匯入的資料逆推。"
        )


def _find_matrix_header(grid: list[list[str]], first_batch_row: int,
                        column: int) -> int | None:
    """往上找最近一列在批次欄右側寫有標題文字的列。"""
    for row_index in range(first_batch_row - 1, max(-1, first_batch_row - 4), -1):
        if row_index < 0:
            break
        row = grid[row_index]
        if any(_cell(row, c) for c in range(column + 1, min(len(row), column + 8))):
            return row_index
    return None


def _section_label(grid: list[list[str]], header_row: int, column: int) -> str:
    """矩陣表頭那一列若只有一格文字（例如 Day 29(Vehicle)），視為區塊標題。"""
    row = grid[header_row]
    values = [v for c, v in enumerate(row) if c > column and v]
    return values[0] if len(values) == 1 else ""
