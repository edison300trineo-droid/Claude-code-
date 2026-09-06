"""從既有的 CSV 試算表匯入案件（支援中文或英文欄位名稱）。

Excel 使用者請先在 Excel 另存為「CSV UTF-8」，即可用此功能一次帶入既有台帳。
"""

import csv
import io
import re

from . import db, models

# 中文標籤 -> 內部欄位；同時接受內部欄位名本身。
HEADER_MAP = {label: field for field, label in models.FIELD_LABELS}
HEADER_MAP.update({field: field for field in models.EDITABLE_FIELDS})
HEADER_MAP.update(
    {
        "編號": "case_no",
        "案號": "case_no",
        "客戶": "client",
        "類型": "case_type",
        "階段": "stage",
        "里程碑": "next_milestone",
        "下一里程碑": "next_milestone",
        "期限": "due_date",
        "到期": "due_date",
        "主持人": "owner",
        "負責": "owner",
        "說明": "notes",
        "合約號": "contract_no",
        "契約編號": "contract_no",
        "合約": "contract_no",
        "研究編號": "study_no",
        "試驗編號": "study_no",
        "研究號": "study_no",
    }
)

DATE_PATTERNS = [
    (re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$"), (1, 2, 3)),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), (1, 2, 3)),
]


def normalise_date(value):
    """把常見的日期寫法轉為 YYYY-MM-DD；無法解析則原樣回傳交給驗證器擋下。"""
    text = (value or "").strip()
    if not text:
        return ""
    for pattern, (y, m, d) in DATE_PATTERNS:
        match = pattern.match(text)
        if match:
            return (
                f"{int(match.group(y)):04d}-{int(match.group(m)):02d}-"
                f"{int(match.group(d)):02d}"
            )
    return text


def parse_rows(text):
    """讀取 CSV 文字，回傳已對應欄位的 dict 清單。"""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    mapping = {}
    for column in reader.fieldnames:
        key = (column or "").strip().lstrip("﻿")
        if key in HEADER_MAP:
            mapping[column] = HEADER_MAP[key]

    if "case_no" not in mapping.values():
        raise ValueError("CSV 缺少「案件編號」欄位，無法匯入")

    rows = []
    for raw in reader:
        item = {}
        for column, field in mapping.items():
            value = (raw.get(column) or "").strip()
            item[field] = normalise_date(value) if field == "due_date" else value
        if item.get("case_no"):
            rows.append(item)
    return rows


def import_csv(conn, path, operator="csv-import", update_existing=True):
    """匯入 CSV，回傳 (新增數, 更新數, 錯誤清單)。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        rows = parse_rows(handle.read())

    created = updated = 0
    errors = []
    for index, row in enumerate(rows, start=2):  # 第 1 列是表頭
        row.setdefault("case_type", models.CASE_TYPES[0])
        row.setdefault("stage", models.STAGES[0])
        row.setdefault("status", models.STATUSES[0])
        try:
            existing = db.get_by_case_no(conn, row["case_no"])
            if existing is None:
                db.create_case(conn, row, operator)
                created += 1
            elif update_existing:
                db.update_case(conn, existing["id"], row, operator)
                updated += 1
        except (models.ValidationError, db.DuplicateCaseNo, db.ConflictError) as exc:
            errors.append(f"第 {index} 列（{row.get('case_no', '?')}）：{exc}")
    return created, updated, errors
