"""從既有的 Excel（.xlsx／.xlsm）或 CSV 檔匯入案件。

設計成能吃「公司原本就在用的表格」：表頭不必在第一列（上面可以有標題列、
製表人、空白列），資料不在第一個工作表也會自動往後找，欄位順序不拘，欄名
支援常見的中文寫法，日期支援西元、民國與 Excel 日期格式。CSV 不論存成
UTF-8 或 Big5（Excel 中文版的預設）都能讀。無法辨識的列會逐列回報原因，
不影響其他列。
"""

import csv
import io
import os
import re

from . import db, models, xlsx_reader

# 中文標籤 -> 內部欄位；同時接受內部欄位名本身。
HEADER_MAP = {label: field for field, label in models.FIELD_LABELS}
HEADER_MAP.update({field: field for field in models.EDITABLE_FIELDS})
HEADER_MAP.update(
    {
        "編號": "case_no",
        "案號": "case_no",
        "案件號": "case_no",
        "報價單號": "case_no",
        "報價編號": "case_no",
        "委託編號": "case_no",
        "專案編號": "case_no",
        "案件名": "title",
        "案名": "title",
        "專案名稱": "title",
        "計畫名稱": "title",
        "試驗名稱": "title",
        "研究名稱": "title",
        "客戶": "client",
        "客戶名": "client",
        "委託單位": "client",
        "類型": "case_type",
        "案件性質": "case_type",
        "試驗類型": "case_type",
        "階段": "stage",
        "進度": "stage",
        "目前進度": "stage",
        "里程碑": "next_milestone",
        "下一里程碑": "next_milestone",
        "下個里程碑": "next_milestone",
        "工作項目": "next_milestone",
        "期限": "due_date",
        "到期": "due_date",
        "預計完成日": "due_date",
        "預計完成日期": "due_date",
        "截止日": "due_date",
        "主持人": "owner",
        "負責": "owner",
        "負責人員": "owner",
        "計畫主持人": "owner",
        "說明": "notes",
        "備考": "notes",
        "合約號": "contract_no",
        "契約編號": "contract_no",
        "合約": "contract_no",
        "研究編號": "study_no",
        "試驗編號": "study_no",
        "研究號": "study_no",
        "試驗代號": "study_no",
    }
)

# 表頭最多往下找幾列（模板常有標題、製表人、空白列）
HEADER_SEARCH_ROWS = 30

DATE_PATTERNS = [
    (re.compile(r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$"), False),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), False),
    (re.compile(r"^(\d{2,3})[/\-.](\d{1,2})[/\-.](\d{1,2})$"), True),  # 民國年
]


# Excel 中文版另存的「CSV（逗號分隔）」是 Big5(cp950)，
# 「CSV UTF-8」才是 UTF-8，兩種都要能讀。
CSV_ENCODINGS = ["utf-8-sig", "cp950", "big5hkscs", "cp936", "utf-16"]


def decode_text(raw):
    """依序嘗試常見編碼，全部失敗才退回可容錯的解碼。"""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for encoding in CSV_ENCODINGS:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _delimiter(text):
    """Excel 的「Unicode 文字」是以 Tab 分隔，一般 CSV 才是逗號。"""
    first = text.split("\n", 1)[0]
    return "\t" if first.count("\t") > first.count(",") else ","


def normalise_header(text):
    return re.sub(r"[\s　*（）():：]+", "", str(text or "")).lstrip("﻿")


def normalise_date(value):
    """把常見日期寫法轉成 YYYY-MM-DD；無法解析則原樣回傳，交給驗證器擋下。"""
    text = str(value or "").strip()
    if not text:
        return ""

    text = text.replace("年", "-").replace("月", "-").replace("日", "")
    text = text.rstrip("-")

    for pattern, minguo in DATE_PATTERNS:
        match = pattern.match(text)
        if match:
            year = int(match.group(1))
            if minguo:
                year += 1911
            return f"{year:04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

    # 儲存格沒設成日期格式時，Excel 會給出天數序號（例如 46296）
    if text.isdigit() and 20000 <= int(text) <= 60000:
        converted = xlsx_reader._serial_to_date(text)
        if converted:
            return converted
    return text


# --------------------------------------------------------------------------
# 讀檔 -> 資料列
# --------------------------------------------------------------------------

def load_rows(source, filename="", sheet=None):
    """讀入檔案，回傳 (資料列, 使用的工作表, 全部工作表名稱)。

    source 可以是路徑字串或檔案內容 bytes；filename 只用來判斷格式。
    """
    name = (filename or (source if isinstance(source, str) else "")).lower()

    if name.endswith((".xlsx", ".xlsm", ".xltx")):
        used, names, rows = xlsx_reader.read_rows(source, sheet)
        return rows, used, names

    if name.endswith(".xls"):
        raise ValueError(
            "不支援舊版 .xls 格式。請用 Excel 開啟後「另存新檔」選 .xlsx 再匯入。"
        )

    if isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
    else:
        with open(source, "rb") as handle:
            raw = handle.read()
    text = decode_text(raw)
    rows = [list(row) for row in csv.reader(io.StringIO(text), delimiter=_delimiter(text))]
    return rows, "", []


def find_header(rows):
    """找出表頭那一列，回傳 (列索引, {欄索引: 欄位名})。"""
    best = None
    for index, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        mapping = {}
        for column, cell in enumerate(row):
            field = HEADER_MAP.get(normalise_header(cell))
            if field and field not in mapping.values():
                mapping[column] = field
        if "case_no" in mapping.values() and (best is None or len(mapping) > len(best[1])):
            best = (index, mapping)

    if best is None:
        raise ValueError(
            "找不到「案件編號」欄位。請確認表格中有一列是欄位名稱，"
            "且其中一欄叫「案件編號」（或編號、案號、報價單號）。"
        )
    return best


def rows_to_items(rows, header_index, mapping):
    """把表頭以下的資料列轉成案件資料，並記住它在檔案中的列號。"""
    items = []
    for offset, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        item = {}
        for column, field in mapping.items():
            value = str(row[column]).strip() if column < len(row) else ""
            item[field] = normalise_date(value) if field == "due_date" else value
        if item.get("case_no"):
            item["_row"] = offset
            items.append(item)
    return items


def is_excel(filename):
    return str(filename or "").lower().endswith((".xlsx", ".xlsm", ".xltx"))


def read_items(source, filename="", sheet=None):
    """讀檔並整理成可匯入的資料，回傳 (items, 讀取資訊)。

    使用者沒指定工作表時，會由第一個工作表往後找，取第一個「有表頭」的
    工作表——公司模板常把封面或說明放在第一頁。
    """
    if is_excel(filename) and not sheet:
        names = xlsx_reader.sheet_names(source)
        for candidate in names:
            rows, used_sheet, sheet_names = load_rows(source, filename, candidate)
            try:
                header_index, mapping = find_header(rows)
            except ValueError:
                continue
            return _build(rows, header_index, mapping, used_sheet, sheet_names)
        raise ValueError(
            f"這個檔案的工作表（{'、'.join(names)}）都找不到「案件編號」欄位。"
            "請確認表格中有一列是欄位名稱，且其中一欄叫「案件編號」"
            "（或編號、案號、報價單號）。"
        )

    rows, used_sheet, sheet_names = load_rows(source, filename, sheet)
    header_index, mapping = find_header(rows)
    return _build(rows, header_index, mapping, used_sheet, sheet_names)


def _build(rows, header_index, mapping, used_sheet, sheet_names):
    items = rows_to_items(rows, header_index, mapping)
    info = {
        "sheet": used_sheet,
        "data_rows": len(rows) - header_index - 1,
        "sheet_names": sheet_names,
        "header_row": header_index + 1,
        "columns": [
            {"column": column, "label": str(rows[header_index][column]), "field": field}
            for column, field in sorted(mapping.items())
        ],
        "ignored_columns": [
            str(cell)
            for column, cell in enumerate(rows[header_index])
            if column not in mapping and str(cell).strip()
        ],
    }
    return items, info


# --------------------------------------------------------------------------
# 預覽與寫入
# --------------------------------------------------------------------------

DEFAULTS = {
    "case_type": models.CASE_TYPES[0],
    "stage": models.STAGES[0],
    "status": models.STATUSES[0],
}


def _prepare(item):
    data = {k: v for k, v in item.items() if not k.startswith("_")}
    for field, default in DEFAULTS.items():
        if not data.get(field):
            data[field] = default
    return data


def plan(conn, items):
    """試算每一列會發生什麼事，不寫入資料庫。"""
    rows = []
    seen = {}
    for item in items:
        data = _prepare(item)
        entry = {
            "row": item.get("_row"),
            "case_no": data.get("case_no", ""),
            "client": data.get("client", ""),
            "case_type": data.get("case_type", ""),
            "due_date": data.get("due_date", ""),
            "owner": data.get("owner", ""),
        }
        try:
            models.validate(data)
        except models.ValidationError as exc:
            entry.update(action="error", message=str(exc))
            rows.append(entry)
            continue

        key = data["case_no"].lower()
        if key in seen:
            entry.update(
                action="error",
                message=f"檔案內第 {seen[key]} 列已有相同案件編號",
            )
        else:
            seen[key] = item.get("_row")
            if db.get_by_case_no(conn, data["case_no"]):
                entry.update(action="update", message="資料庫已有此編號，將更新內容")
            else:
                entry.update(action="create", message="")
        rows.append(entry)

    totals = {"create": 0, "update": 0, "error": 0, "total": len(rows)}
    for entry in rows:
        totals[entry["action"]] += 1
    return {"rows": rows, "totals": totals}


def commit(conn, items, operator="csv-import", update_existing=True):
    """實際寫入，回傳 (新增數, 更新數, 略過數, 錯誤訊息清單)。"""
    created = updated = skipped = 0
    errors = []
    for item in items:
        data = _prepare(item)
        row_no = item.get("_row", "?")
        try:
            existing = db.get_by_case_no(conn, data.get("case_no", ""))
            if existing is None:
                db.create_case(conn, data, operator)
                created += 1
            elif update_existing:
                db.update_case(conn, existing["id"], data, operator)
                updated += 1
            else:
                skipped += 1
        except (models.ValidationError, db.DuplicateCaseNo, db.ConflictError) as exc:
            errors.append(f"第 {row_no} 列（{data.get('case_no', '?')}）：{exc}")
    return created, updated, skipped, errors


def import_file(conn, path, operator="import", update_existing=True, sheet=None):
    """命令列用的一次完成版本。"""
    items, info = read_items(path, os.path.basename(path), sheet)
    created, updated, skipped, errors = commit(conn, items, operator, update_existing)
    return created, updated, skipped, errors, info
