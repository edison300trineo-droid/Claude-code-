"""以標準函式庫 http.server 實作的多執行緒小型伺服器。"""

import json
import mimetypes
import os
import re
import urllib.parse
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, db, export, importer, models, report, template

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

MAX_BODY = 1 * 1024 * 1024  # 1 MB，足夠單筆案件
MAX_UPLOAD = 20 * 1024 * 1024  # 匯入用的 Excel／CSV 上限

CASE_ID_RE = re.compile(r"^/api/cases/(\d+)$")
CASE_HISTORY_RE = re.compile(r"^/api/cases/(\d+)/history$")


class ApiError(Exception):
    def __init__(self, status, message, details=None):
        self.status = status
        self.message = message
        self.details = details or {}
        super().__init__(message)


def _safe_static_path(path):
    """把 URL 路徑對應到 static 目錄下的實體檔案，阻擋目錄跳脫。"""
    relative = urllib.parse.unquote(path.lstrip("/"))
    target = os.path.normpath(os.path.join(STATIC_DIR, relative))
    if target != STATIC_DIR and not target.startswith(STATIC_DIR + os.sep):
        return None
    return target if os.path.isfile(target) else None


def _content_disposition(filename):
    quoted = urllib.parse.quote(filename)
    return f"attachment; filename=\"export.bin\"; filename*=UTF-8''{quoted}"


class Handler(BaseHTTPRequestHandler):
    server_version = f"CaseTracker/{__version__}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------
    # 基礎工具
    # ------------------------------------------------------------------
    def log_message(self, fmt, *args):
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send(self, status, body=b"", content_type="text/plain; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, status, message, details=None):
        self._send_json(
            {"error": message, "details": details or {}}, status=status
        )

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "請求內容過大")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "請求內容不是合法的 JSON")
        if not isinstance(payload, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "請求內容須為 JSON 物件")
        return payload

    def _read_binary(self):
        """讀取上傳的檔案內容（前端直接送出原始位元組，不用 multipart）。"""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ApiError(HTTPStatus.BAD_REQUEST, "沒有收到檔案內容")
        if length > MAX_UPLOAD:
            raise ApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"檔案超過 {MAX_UPLOAD // (1024 * 1024)} MB 上限",
            )
        data = bytearray()
        while len(data) < length:
            chunk = self.rfile.read(min(65536, length - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) < length:
            raise ApiError(HTTPStatus.BAD_REQUEST, "檔案上傳未完成，請重試")
        return bytes(data)

    def _operator(self, query, payload=None):
        """操作者名稱：body > query > header。"""
        for candidate in (
            (payload or {}).get("_operator"),
            (query.get("operator") or [""])[0],
            urllib.parse.unquote(self.headers.get("X-Operator", "")),
        ):
            if candidate and str(candidate).strip():
                return str(candidate).strip()[:60]
        return ""

    @staticmethod
    def _filters_from_query(query):
        def one(key):
            return (query.get(key) or [""])[0]

        def flag(key):
            return one(key).lower() in ("1", "true", "yes", "on")

        return {
            "q": one("q"),
            "status": one("status"),
            "case_type": one("case_type"),
            "stage": one("stage"),
            "owner": one("owner"),
            "sort": one("sort"),
            "dir": one("dir"),
            "open_only": flag("open_only"),
            "overdue_only": flag("overdue_only"),
            "attention_only": flag("attention_only"),
        }

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        try:
            if path.startswith("/api/") or path.startswith("/export/"):
                self._handle_api(method, path, query)
            elif method == "GET":
                self._handle_static(path)
            else:
                self._send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "不支援的方法")
        except ApiError as exc:
            self._send_error_json(exc.status, exc.message, exc.details)
        except models.ValidationError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "欄位驗證失敗", exc.errors)
        except db.ConflictError as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
        except db.DuplicateCaseNo as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
        except db.NotFound as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001 - 統一回報 500，避免洩漏堆疊
            self.log_error("未預期的錯誤: %r", exc)
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, f"伺服器內部錯誤：{exc}"
            )

    def _handle_static(self, path):
        if path == "/":
            path = "/index.html"
        target = _safe_static_path(path)
        if target is None:
            self._send(HTTPStatus.NOT_FOUND, "找不到頁面".encode("utf-8"))
            return
        content_type, _ = mimetypes.guess_type(target)
        if content_type is None:
            content_type = "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript",
            "application/json",
        ):
            content_type += "; charset=utf-8"
        with open(target, "rb") as handle:
            body = handle.read()
        self._send(HTTPStatus.OK, body, content_type)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def _handle_api(self, method, path, query):
        conn = db.connect()
        today = date.today()

        if path == "/api/meta" and method == "GET":
            self._send_json(
                {
                    "version": __version__,
                    "today": today.isoformat(),
                    "case_types": models.CASE_TYPES,
                    "stages": models.STAGES,
                    "statuses": models.STATUSES,
                    "due_soon_days": models.DUE_SOON_DAYS,
                    "field_labels": dict(models.FIELD_LABELS),
                    "owners": db.distinct_values(conn, "owner"),
                    "clients": db.distinct_values(conn, "client"),
                    "contracts": db.distinct_values(conn, "contract_no"),
                    "status_counts": db.counts_by_status(conn),
                }
            )
            return

        if path == "/api/cases":
            if method == "GET":
                items = db.list_cases(conn, self._filters_from_query(query), today)
                self._send_json(
                    {"items": items, "count": len(items), "today": today.isoformat()}
                )
                return
            if method == "POST":
                payload = self._read_json()
                operator = self._operator(query, payload)
                case = db.create_case(conn, payload, operator)
                self._send_json(case, status=HTTPStatus.CREATED)
                return
            raise ApiError(HTTPStatus.METHOD_NOT_ALLOWED, "不支援的方法")

        match = CASE_HISTORY_RE.match(path)
        if match and method == "GET":
            self._send_json({"items": db.history(conn, int(match.group(1)))})
            return

        match = CASE_ID_RE.match(path)
        if match:
            case_id = int(match.group(1))
            if method == "GET":
                self._send_json(db.get_case(conn, case_id, today))
                return
            if method == "PUT":
                payload = self._read_json()
                operator = self._operator(query, payload)
                expected_rev = payload.pop("rev", None)
                payload.pop("_operator", None)
                case = db.update_case(conn, case_id, payload, operator, expected_rev)
                self._send_json(case)
                return
            if method == "DELETE":
                operator = self._operator(query)
                db.delete_case(conn, case_id, operator)
                self._send_json({"deleted": case_id})
                return
            raise ApiError(HTTPStatus.METHOD_NOT_ALLOWED, "不支援的方法")

        if path in ("/api/import/preview", "/api/import/commit") and method == "POST":
            self._handle_import(conn, path, query)
            return

        if path == "/api/activity" and method == "GET":
            self._send_json({"items": db.recent_activity(conn)})
            return

        if path == "/api/report/weekly" and method == "GET":
            days = self._window_days(query)
            self._send_json(report.weekly_summary(conn, today, days))
            return

        if path.startswith("/export/") and method == "GET":
            self._handle_export(conn, path, query, today)
            return

        raise ApiError(HTTPStatus.NOT_FOUND, f"未知的 API 路徑：{path}")

    def _handle_import(self, conn, path, query):
        filename = (query.get("filename") or [""])[0]
        sheet = (query.get("sheet") or [""])[0] or None
        update_existing = (query.get("update_existing") or ["1"])[0] != "0"
        data = self._read_binary()

        try:
            items, info = importer.read_items(data, filename, sheet)
        except ValueError as exc:  # 檔案格式或表頭問題，訊息可直接給使用者看
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc))

        if path.endswith("/preview"):
            result = importer.plan(conn, items)
            self._send_json({"info": info, **result})
            return

        operator = self._operator(query) or "import"
        created, updated, skipped, errors = importer.commit(
            conn, items, operator, update_existing
        )
        self._send_json(
            {
                "info": info,
                "created": created,
                "updated": updated,
                "skipped": skipped,
                "errors": errors,
            }
        )

    @staticmethod
    def _window_days(query):
        raw = (query.get("days") or [""])[0]
        try:
            days = int(raw)
        except (TypeError, ValueError):
            return models.DUE_SOON_DAYS
        return max(0, min(days, 365))

    def _handle_export(self, conn, path, query, today):
        stamp = today.isoformat()
        if path in ("/export/cases.csv", "/export/cases.xlsx"):
            items = db.list_cases(conn, self._filters_from_query(query), today)
            if path.endswith(".csv"):
                body = export.to_csv(items)
                mime = "text/csv; charset=utf-8"
                filename = f"案件清單_{stamp}.csv"
            else:
                body = export.to_xlsx(items)
                mime = (
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"
                )
                filename = f"案件清單_{stamp}.xlsx"
        elif path == "/export/template.xlsx":
            body = template.build()
            mime = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            filename = "案件匯入範本.xlsx"
        elif path in ("/export/weekly.csv", "/export/weekly.xlsx", "/export/weekly.txt"):
            summary = report.weekly_summary(conn, today, self._window_days(query))
            if path.endswith(".csv"):
                body = report.to_csv(summary)
                mime = "text/csv; charset=utf-8"
                filename = f"到期摘要_{stamp}.csv"
            elif path.endswith(".txt"):
                body = report.to_text(summary).encode("utf-8-sig")
                mime = "text/plain; charset=utf-8"
                filename = f"到期摘要_{stamp}.txt"
            else:
                body = report.to_xlsx(summary)
                mime = (
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"
                )
                filename = f"到期摘要_{stamp}.xlsx"
        else:
            raise ApiError(HTTPStatus.NOT_FOUND, f"未知的匯出路徑：{path}")

        self._send(
            HTTPStatus.OK,
            body,
            mime,
            {"Content-Disposition": _content_disposition(filename)},
        )


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, verbose=False):
        super().__init__(address, Handler)
        self.verbose = verbose


def serve(host="0.0.0.0", port=8765, verbose=False):
    httpd = Server((host, port), verbose=verbose)
    return httpd
