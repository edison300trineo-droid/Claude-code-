"""SQLite 資料層。

設計重點（多人同時使用）：
- WAL 模式 + busy_timeout，允許多讀者與單一寫入者併行。
- 每個執行緒各自持有連線（http.server 為多執行緒）。
- 以 rev 欄位做樂觀鎖：更新時 rev 不符即回報衝突，避免互相覆蓋。
"""

import os
import sqlite3
import threading
from datetime import datetime

from . import models

SCHEMA_VERSION = 2

_local = threading.local()
_db_path = None
_write_lock = threading.Lock()


class ConflictError(Exception):
    """資料已被他人更新（樂觀鎖失敗）。"""


class DuplicateCaseNo(Exception):
    """案件編號重複。"""


class NotFound(Exception):
    """查無此案件。"""


def now_stamp():
    return datetime.now().isoformat(timespec="seconds")


def configure(path):
    """設定資料庫檔案路徑並建立結構。"""
    global _db_path
    _db_path = os.path.abspath(path)
    directory = os.path.dirname(_db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = connect()
    init_schema(conn)
    return _db_path


def connect():
    """取得本執行緒專用的連線。"""
    if _db_path is None:
        raise RuntimeError("尚未呼叫 db.configure() 設定資料庫路徑")
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != _db_path:
        conn = sqlite3.connect(_db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        _local.conn = conn
        _local.path = _db_path
    return conn


def close_thread_connection():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def init_schema(conn):
    with _write_lock, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                case_no        TEXT NOT NULL COLLATE NOCASE,
                contract_no    TEXT NOT NULL DEFAULT '',
                study_no       TEXT NOT NULL DEFAULT '',
                client         TEXT NOT NULL DEFAULT '',
                case_type      TEXT NOT NULL,
                stage          TEXT NOT NULL,
                next_milestone TEXT NOT NULL DEFAULT '',
                due_date       TEXT NOT NULL DEFAULT '',
                owner          TEXT NOT NULL DEFAULT '',
                status         TEXT NOT NULL,
                notes          TEXT NOT NULL DEFAULT '',
                rev            INTEGER NOT NULL DEFAULT 1,
                created_at     TEXT NOT NULL,
                created_by     TEXT NOT NULL DEFAULT '',
                updated_at     TEXT NOT NULL,
                updated_by     TEXT NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_case_no ON cases(case_no);
            CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
            CREATE INDEX IF NOT EXISTS idx_cases_due ON cases(due_date);
            CREATE INDEX IF NOT EXISTS idx_cases_owner ON cases(owner);

            CREATE TABLE IF NOT EXISTS case_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id    INTEGER NOT NULL,
                case_no    TEXT NOT NULL,
                action     TEXT NOT NULL,
                field      TEXT NOT NULL DEFAULT '',
                old_value  TEXT NOT NULL DEFAULT '',
                new_value  TEXT NOT NULL DEFAULT '',
                operator   TEXT NOT NULL DEFAULT '',
                changed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_history_case ON case_history(case_id);
            """
        )
        _migrate(conn)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _migrate(conn):
    """為既有資料庫補上後來新增的欄位（v1 -> v2：合約編號、研究編號）。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(cases)")}
    for column in ("contract_no", "study_no"):
        if column not in existing:
            conn.execute(
                f"ALTER TABLE cases ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
            )
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_cases_contract ON cases(contract_no);
        CREATE INDEX IF NOT EXISTS idx_cases_study ON cases(study_no);
        """
    )


# --------------------------------------------------------------------------
# 查詢
# --------------------------------------------------------------------------

SORT_COLUMNS = {
    "case_no": "case_no",
    "contract_no": "contract_no",
    "study_no": "study_no",
    "client": "client",
    "case_type": "case_type",
    "stage": "stage",
    "due_date": "due_date",
    "owner": "owner",
    "status": "status",
    "updated_at": "updated_at",
}


def _build_filters(filters):
    where, params = [], []
    status = (filters.get("status") or "").strip()
    if status:
        where.append("status = ?")
        params.append(status)

    case_type = (filters.get("case_type") or "").strip()
    if case_type:
        where.append("case_type = ?")
        params.append(case_type)

    stage = (filters.get("stage") or "").strip()
    if stage:
        where.append("stage = ?")
        params.append(stage)

    owner = (filters.get("owner") or "").strip()
    if owner:
        where.append("owner = ?")
        params.append(owner)

    contract_no = (filters.get("contract_no") or "").strip()
    if contract_no:
        where.append("contract_no = ?")
        params.append(contract_no)

    query = (filters.get("q") or "").strip()
    if query:
        like = f"%{query}%"
        where.append(
            "(case_no LIKE ? OR contract_no LIKE ? OR study_no LIKE ?"
            " OR client LIKE ?)"
        )
        params.extend([like] * 4)

    if filters.get("open_only"):
        where.append("status <> ?")
        params.append(models.CLOSED_STATUS)

    return where, params


def list_cases(conn, filters=None, today=None):
    """依條件列出案件；overdue_only 等衍生條件在 Python 端處理。"""
    filters = filters or {}
    where, params = _build_filters(filters)

    sort = filters.get("sort") or ""
    direction = "DESC" if str(filters.get("dir", "")).lower() == "desc" else "ASC"
    if sort in SORT_COLUMNS:
        column = SORT_COLUMNS[sort]
        if column == "due_date":
            order = f"(due_date = '') ASC, due_date {direction}, case_no ASC"
        else:
            order = f"{column} {direction}, case_no ASC"
    else:
        # 預設：未結案優先，再依到期日由近到遠，無到期日者殿後。
        order = (
            f"(status = '{models.CLOSED_STATUS}') ASC, "
            "(due_date = '') ASC, due_date ASC, case_no ASC"
        )

    sql = "SELECT * FROM cases"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + order

    rows = [models.decorate(row, today) for row in conn.execute(sql, params)]

    if filters.get("overdue_only"):
        rows = [r for r in rows if r["due_state"] == "overdue"]
    elif filters.get("attention_only"):
        rows = [r for r in rows if r["due_state"] in ("overdue", "due_soon")]
    return rows


def get_case(conn, case_id, today=None):
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        raise NotFound(f"查無案件 id={case_id}")
    return models.decorate(row, today)


def get_by_case_no(conn, case_no, today=None):
    row = conn.execute(
        "SELECT * FROM cases WHERE case_no = ? COLLATE NOCASE", (case_no,)
    ).fetchone()
    return models.decorate(row, today) if row else None


def distinct_values(conn, column):
    if column not in ("owner", "client", "contract_no"):
        raise ValueError(f"不支援的欄位：{column}")
    rows = conn.execute(
        f"SELECT DISTINCT {column} AS v FROM cases WHERE {column} <> '' ORDER BY {column}"
    )
    return [r["v"] for r in rows]


def counts_by_status(conn):
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM cases GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


# --------------------------------------------------------------------------
# 寫入
# --------------------------------------------------------------------------

def _log(conn, case_id, case_no, action, field, old, new, operator, stamp):
    conn.execute(
        "INSERT INTO case_history (case_id, case_no, action, field, old_value,"
        " new_value, operator, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (case_id, case_no, action, field, old or "", new or "", operator, stamp),
    )


def create_case(conn, payload, operator=""):
    data = models.validate(payload)
    stamp = now_stamp()
    with _write_lock, conn:
        exists = conn.execute(
            "SELECT 1 FROM cases WHERE case_no = ? COLLATE NOCASE", (data["case_no"],)
        ).fetchone()
        if exists:
            raise DuplicateCaseNo(f"案件編號 {data['case_no']} 已存在")
        cursor = conn.execute(
            "INSERT INTO cases (case_no, contract_no, study_no, client, case_type,"
            " stage, next_milestone, due_date, owner, status, notes, rev,"
            " created_at, created_by, updated_at, updated_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (
                data["case_no"], data["contract_no"], data["study_no"],
                data["client"], data["case_type"], data["stage"],
                data["next_milestone"], data["due_date"], data["owner"],
                data["status"], data["notes"], stamp, operator, stamp, operator,
            ),
        )
        case_id = cursor.lastrowid
        _log(conn, case_id, data["case_no"], "create", "", "", "", operator, stamp)
    return get_case(conn, case_id)


def update_case(conn, case_id, payload, operator="", expected_rev=None):
    stamp = now_stamp()
    with _write_lock, conn:
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise NotFound(f"查無案件 id={case_id}")
        data = models.validate(payload, partial=True)
        if expected_rev is not None and int(expected_rev) != row["rev"]:
            raise ConflictError(
                f"此案件已由 {row['updated_by'] or '他人'} 於 {row['updated_at']} 更新，請重新載入後再編輯"
            )

        if "case_no" in data and data["case_no"].lower() != row["case_no"].lower():
            dup = conn.execute(
                "SELECT 1 FROM cases WHERE case_no = ? COLLATE NOCASE AND id <> ?",
                (data["case_no"], case_id),
            ).fetchone()
            if dup:
                raise DuplicateCaseNo(f"案件編號 {data['case_no']} 已存在")

        changed = {k: v for k, v in data.items() if (row[k] or "") != v}
        if not changed:
            return models.decorate(row)

        assignments = ", ".join(f"{k} = ?" for k in changed)
        params = list(changed.values()) + [stamp, operator, case_id]
        conn.execute(
            f"UPDATE cases SET {assignments}, rev = rev + 1, updated_at = ?,"
            " updated_by = ? WHERE id = ?",
            params,
        )
        case_no = changed.get("case_no", row["case_no"])
        for field, value in changed.items():
            _log(conn, case_id, case_no, "update", field, row[field], value, operator, stamp)
    return get_case(conn, case_id)


def delete_case(conn, case_id, operator=""):
    stamp = now_stamp()
    with _write_lock, conn:
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise NotFound(f"查無案件 id={case_id}")
        conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))
        _log(conn, case_id, row["case_no"], "delete", "", row["case_no"], "", operator, stamp)
    return dict(row)


def history(conn, case_id, limit=200):
    rows = conn.execute(
        "SELECT * FROM case_history WHERE case_id = ? ORDER BY id DESC LIMIT ?",
        (case_id, limit),
    )
    return [dict(r) for r in rows]


def recent_activity(conn, limit=50):
    rows = conn.execute(
        "SELECT * FROM case_history ORDER BY id DESC LIMIT ?", (limit,)
    )
    return [dict(r) for r in rows]
