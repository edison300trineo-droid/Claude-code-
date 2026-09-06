"""單元測試：資料層、驗證、匯出、摘要與 HTTP API。"""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case_tracker import db, export, importer, models, report, server  # noqa: E402

TODAY = date(2026, 9, 6)


def sample(**overrides):
    data = {
        "case_no": "TN-VI1141101-R01",
        "client": "宏碩生技",
        "case_type": "GLP 研究",
        "stage": "試驗執行中",
        "next_milestone": "第 28 天中期報告",
        "due_date": "2026-09-20",
        "owner": "陳彥廷",
        "status": "進行中",
        "notes": "SD 大鼠 28 天重複劑量毒性",
    }
    data.update(overrides)
    return data


class TempDbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.close_thread_connection()
        db.configure(os.path.join(self.tmp.name, "cases.db"))
        self.conn = db.connect()

    def tearDown(self):
        db.close_thread_connection()
        self.tmp.cleanup()


class ValidationTests(unittest.TestCase):
    def test_rejects_empty_case_no(self):
        with self.assertRaises(models.ValidationError) as ctx:
            models.validate(sample(case_no="  "))
        self.assertIn("case_no", ctx.exception.errors)

    def test_rejects_unknown_enum(self):
        for field, value in [("case_type", "臨床試驗"), ("stage", "未知"), ("status", "暫停")]:
            with self.subTest(field=field):
                with self.assertRaises(models.ValidationError):
                    models.validate(sample(**{field: value}))

    def test_rejects_bad_date(self):
        with self.assertRaises(models.ValidationError):
            models.validate(sample(due_date="2026/13/45"))

    def test_allows_blank_date_and_trims(self):
        clean = models.validate(sample(due_date="", client="  光宇製藥 "))
        self.assertEqual(clean["due_date"], "")
        self.assertEqual(clean["client"], "光宇製藥")

    def test_partial_validation_only_checks_given_fields(self):
        clean = models.validate({"status": "已結案"}, partial=True)
        self.assertEqual(clean, {"status": "已結案"})


class DueStateTests(unittest.TestCase):
    def test_overdue(self):
        state, days, label = models.due_state("2026-09-01", "進行中", TODAY)
        self.assertEqual(state, "overdue")
        self.assertEqual(days, -5)
        self.assertEqual(label, "逾期 5 天")

    def test_due_today_and_soon(self):
        self.assertEqual(models.due_state("2026-09-06", "進行中", TODAY)[0], "due_soon")
        self.assertEqual(models.due_state("2026-09-13", "進行中", TODAY)[0], "due_soon")
        self.assertEqual(models.due_state("2026-09-14", "進行中", TODAY)[0], "scheduled")

    def test_closed_case_is_never_overdue(self):
        self.assertEqual(models.due_state("2026-01-01", "已結案", TODAY)[0], "closed")

    def test_missing_date(self):
        self.assertEqual(models.due_state("", "進行中", TODAY)[0], "none")


class CrudTests(TempDbTestCase):
    def test_create_and_get(self):
        created = db.create_case(self.conn, sample(), "測試員")
        self.assertEqual(created["rev"], 1)
        self.assertEqual(created["created_by"], "測試員")
        fetched = db.get_case(self.conn, created["id"])
        self.assertEqual(fetched["case_no"], "TN-VI1141101-R01")

    def test_duplicate_case_no_rejected_case_insensitively(self):
        db.create_case(self.conn, sample())
        with self.assertRaises(db.DuplicateCaseNo):
            db.create_case(self.conn, sample(case_no="tn-vi1141101-r01"))

    def test_update_bumps_rev_and_logs_history(self):
        created = db.create_case(self.conn, sample())
        updated = db.update_case(
            self.conn, created["id"], {"stage": "QA 審查", "status": "需留意"}, "林郁涵"
        )
        self.assertEqual(updated["rev"], 2)
        self.assertEqual(updated["stage"], "QA 審查")
        fields = {h["field"] for h in db.history(self.conn, created["id"]) if h["action"] == "update"}
        self.assertEqual(fields, {"stage", "status"})

    def test_stale_rev_raises_conflict(self):
        created = db.create_case(self.conn, sample())
        db.update_case(self.conn, created["id"], {"owner": "甲"}, "A", expected_rev=1)
        with self.assertRaises(db.ConflictError):
            db.update_case(self.conn, created["id"], {"owner": "乙"}, "B", expected_rev=1)

    def test_no_op_update_keeps_rev(self):
        created = db.create_case(self.conn, sample())
        same = db.update_case(self.conn, created["id"], {"owner": created["owner"]})
        self.assertEqual(same["rev"], 1)

    def test_delete(self):
        created = db.create_case(self.conn, sample())
        db.delete_case(self.conn, created["id"], "測試員")
        with self.assertRaises(db.NotFound):
            db.get_case(self.conn, created["id"])

    def test_update_missing_case(self):
        with self.assertRaises(db.NotFound):
            db.update_case(self.conn, 999, {"owner": "甲"})


class QueryTests(TempDbTestCase):
    def setUp(self):
        super().setUp()
        db.create_case(self.conn, sample(case_no="TN-A-01", client="宏碩生技", due_date="2026-09-01"))
        db.create_case(
            self.conn,
            sample(case_no="TN-B-02", client="光宇製藥", status="已結案",
                   case_type="稽核", due_date="2026-08-01"),
        )
        db.create_case(
            self.conn,
            sample(case_no="TN-C-03", client="英傑生醫", due_date="", owner="林郁涵"),
        )

    def test_search_matches_case_no_or_client(self):
        self.assertEqual(len(db.list_cases(self.conn, {"q": "TN-B"})), 1)
        self.assertEqual(len(db.list_cases(self.conn, {"q": "光宇"})), 1)

    def test_status_and_type_filters(self):
        self.assertEqual(len(db.list_cases(self.conn, {"status": "已結案"})), 1)
        self.assertEqual(len(db.list_cases(self.conn, {"case_type": "稽核"})), 1)

    def test_open_only_and_overdue_only(self):
        self.assertEqual(len(db.list_cases(self.conn, {"open_only": True})), 2)
        overdue = db.list_cases(self.conn, {"overdue_only": True}, today=TODAY)
        self.assertEqual([c["case_no"] for c in overdue], ["TN-A-01"])

    def test_default_order_puts_undated_and_closed_last(self):
        order = [c["case_no"] for c in db.list_cases(self.conn, {}, today=TODAY)]
        self.assertEqual(order, ["TN-A-01", "TN-C-03", "TN-B-02"])

    def test_explicit_sort(self):
        order = [
            c["case_no"]
            for c in db.list_cases(self.conn, {"sort": "case_no", "dir": "desc"})
        ]
        self.assertEqual(order, ["TN-C-03", "TN-B-02", "TN-A-01"])

    def test_distinct_owners(self):
        self.assertIn("林郁涵", db.distinct_values(self.conn, "owner"))


class ReportTests(TempDbTestCase):
    def setUp(self):
        super().setUp()
        day = lambda n: (TODAY + timedelta(days=n)).isoformat()  # noqa: E731
        db.create_case(self.conn, sample(case_no="TN-OVERDUE", due_date=day(-4)))
        db.create_case(self.conn, sample(case_no="TN-SOON", due_date=day(3)))
        db.create_case(self.conn, sample(case_no="TN-LATER", due_date=day(30)))
        db.create_case(self.conn, sample(case_no="TN-NODATE", due_date=""))
        db.create_case(self.conn, sample(case_no="TN-DONE", due_date=day(-9), status="已結案"))

    def test_buckets(self):
        summary = report.weekly_summary(self.conn, TODAY, 7)
        self.assertEqual([c["case_no"] for c in summary["overdue"]], ["TN-OVERDUE"])
        self.assertEqual([c["case_no"] for c in summary["upcoming"]], ["TN-SOON"])
        self.assertEqual([c["case_no"] for c in summary["undated"]], ["TN-NODATE"])
        self.assertEqual(summary["totals"]["active"], 4)

    def test_wider_window_includes_more(self):
        summary = report.weekly_summary(self.conn, TODAY, 30)
        self.assertEqual(
            [c["case_no"] for c in summary["upcoming"]], ["TN-SOON", "TN-LATER"]
        )

    def test_text_and_exports(self):
        summary = report.weekly_summary(self.conn, TODAY, 7)
        text = report.to_text(summary)
        self.assertIn("TN-OVERDUE", text)
        self.assertIn("已逾期", text)
        self.assertTrue(report.to_csv(summary).startswith(b"\xef\xbb\xbf"))
        self.assertTrue(report.to_xlsx(summary).startswith(b"PK"))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.items = [models.decorate(sample(), TODAY), models.decorate(sample(case_no="TN-B", notes="含 < > & 特殊字元"), TODAY)]

    def test_csv_has_bom_and_headers(self):
        raw = export.to_csv(self.items)
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8-sig")
        self.assertTrue(text.startswith("案件編號,客戶名稱"))
        self.assertIn("TN-VI1141101-R01", text)

    def test_xlsx_structure(self):
        raw = export.to_xlsx(self.items)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            self.assertLessEqual(
                {
                    "[Content_Types].xml",
                    "_rels/.rels",
                    "xl/workbook.xml",
                    "xl/_rels/workbook.xml.rels",
                    "xl/styles.xml",
                    "xl/worksheets/sheet1.xml",
                },
                names,
            )
            for name in names:
                ElementTree.fromstring(archive.read(name))  # 每個 part 都要是合法 XML
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("案件編號", sheet)
        self.assertIn("&lt; &gt; &amp;", sheet)  # 特殊字元有被逸出

    def test_due_date_written_as_excel_serial(self):
        raw = export.to_xlsx([models.decorate(sample(due_date="2026-09-20"), TODAY)])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        expected = (date(2026, 9, 20) - export.EXCEL_EPOCH).days
        self.assertIn(f"<v>{expected}</v>", sheet)

    def test_column_letters(self):
        self.assertEqual(export._col_letter(1), "A")
        self.assertEqual(export._col_letter(26), "Z")
        self.assertEqual(export._col_letter(27), "AA")


class ImportTests(TempDbTestCase):
    def _write(self, text):
        path = os.path.join(self.tmp.name, "in.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(text)
        return path

    def test_import_chinese_headers(self):
        path = self._write(
            "案件編號,客戶名稱,案件類型,目前階段,到期日,負責人,狀態\r\n"
            "TN-IMP-01,宏碩生技,GLP 研究,試驗執行中,2026/9/20,陳彥廷,進行中\r\n"
            "TN-IMP-02,光宇製藥,稽核,QA 審查,20261001,王孟儒,需留意\r\n"
        )
        created, updated, errors = importer.import_csv(self.conn, path)
        self.assertEqual((created, updated, errors), (2, 0, []))
        row = db.get_by_case_no(self.conn, "TN-IMP-01")
        self.assertEqual(row["due_date"], "2026-09-20")
        self.assertEqual(db.get_by_case_no(self.conn, "TN-IMP-02")["due_date"], "2026-10-01")

    def test_reimport_updates_existing(self):
        path = self._write("案件編號,負責人\r\nTN-IMP-01,甲\r\n")
        importer.import_csv(self.conn, path)
        path2 = self._write("案件編號,負責人\r\nTN-IMP-01,乙\r\n")
        created, updated, errors = importer.import_csv(self.conn, path2)
        self.assertEqual((created, updated), (0, 1))
        self.assertEqual(db.get_by_case_no(self.conn, "TN-IMP-01")["owner"], "乙")

    def test_bad_rows_are_reported_not_fatal(self):
        path = self._write(
            "案件編號,案件類型\r\nTN-OK,稽核\r\nTN-BAD,不存在的類型\r\n"
        )
        created, _, errors = importer.import_csv(self.conn, path)
        self.assertEqual(created, 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("TN-BAD", errors[0])

    def test_missing_case_no_column(self):
        path = self._write("客戶名稱\r\n宏碩生技\r\n")
        with self.assertRaises(ValueError):
            importer.import_csv(self.conn, path)


class ApiTests(TempDbTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()

    def setUp(self):
        super().setUp()
        self.httpd = server.serve("127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def request(self, method, path, payload=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        req.add_header("X-Operator", "%E6%B8%AC%E8%A9%A6%E5%93%A1")  # 測試員
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def json_request(self, method, path, payload=None):
        status, body, _ = self.request(method, path, payload)
        return status, json.loads(body.decode("utf-8"))

    def test_index_and_static_are_served(self):
        status, body, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("案件追蹤台帳", body.decode("utf-8"))
        self.assertIn("text/html", headers["Content-Type"])
        self.assertEqual(self.request("GET", "/app.js")[0], 200)
        self.assertEqual(self.request("GET", "/style.css")[0], 200)

    def test_directory_traversal_blocked(self):
        self.assertEqual(self.request("GET", "/../run.py")[0], 404)

    def test_meta_lists_enums(self):
        status, payload = self.json_request("GET", "/api/meta")
        self.assertEqual(status, 200)
        self.assertEqual(payload["stages"], models.STAGES)
        self.assertEqual(payload["case_types"], models.CASE_TYPES)

    def test_crud_round_trip(self):
        status, created = self.json_request("POST", "/api/cases", sample())
        self.assertEqual(status, 201)
        self.assertEqual(created["created_by"], "測試員")
        case_id = created["id"]

        status, listing = self.json_request("GET", "/api/cases?q=TN-VI")
        self.assertEqual(listing["count"], 1)

        status, updated = self.json_request(
            "PUT", f"/api/cases/{case_id}", {"stage": "QA 審查", "rev": created["rev"]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["stage"], "QA 審查")

        status, conflict = self.json_request(
            "PUT", f"/api/cases/{case_id}", {"stage": "結案", "rev": created["rev"]}
        )
        self.assertEqual(status, 409)
        self.assertIn("重新載入", conflict["error"])

        status, history = self.json_request("GET", f"/api/cases/{case_id}/history")
        self.assertTrue(any(h["field"] == "stage" for h in history["items"]))

        status, _ = self.json_request("DELETE", f"/api/cases/{case_id}")
        self.assertEqual(status, 200)
        self.assertEqual(self.json_request("GET", f"/api/cases/{case_id}")[0], 404)

    def test_validation_error_returns_400_with_details(self):
        status, payload = self.json_request("POST", "/api/cases", sample(case_no=""))
        self.assertEqual(status, 400)
        self.assertIn("case_no", payload["details"])

    def test_duplicate_returns_409(self):
        self.json_request("POST", "/api/cases", sample())
        status, _ = self.json_request("POST", "/api/cases", sample())
        self.assertEqual(status, 409)

    def test_exports_download(self):
        self.json_request("POST", "/api/cases", sample())
        status, body, headers = self.request("GET", "/export/cases.csv")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        self.assertIn("filename*=UTF-8''", headers["Content-Disposition"])

        status, body, headers = self.request("GET", "/export/cases.xlsx?open_only=1")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"PK"))
        self.assertIn("spreadsheetml", headers["Content-Type"])

    def test_weekly_report_endpoint(self):
        self.json_request("POST", "/api/cases", sample(due_date="2000-01-01"))
        status, payload = self.json_request("GET", "/api/report/weekly?days=7")
        self.assertEqual(status, 200)
        self.assertEqual(payload["totals"]["overdue"], 1)

    def test_unknown_api_path(self):
        self.assertEqual(self.request("GET", "/api/nope")[0], 404)

    def test_bad_json_body(self):
        url = f"http://127.0.0.1:{self.port}/api/cases"
        req = urllib.request.Request(url, data=b"{not json", method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
