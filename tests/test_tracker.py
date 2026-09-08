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

from case_tracker import (  # noqa: E402
    db, export, importer, models, report, server, template, xlsx_reader,
)

TODAY = date(2026, 9, 6)


def sample(**overrides):
    data = {
        "case_no": "QT114001",
        "contract_no": "C-114-021",
        "study_no": "TMT-114-003",
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


SHEET_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c></row>
<row r="3"><c r="A3" t="s"><v>1</v></c><c r="B3" t="s"><v>2</v></c>
<c r="C3" t="s"><v>3</v></c><c r="E3" t="s"><v>4</v></c></row>
<row r="4"><c r="A4" t="s"><v>5</v></c><c r="B4" t="s"><v>6</v></c>
<c r="C4" s="1"><v>46310</v></c><c r="E4" t="s"><v>7</v></c></row>
<row r="5"><c r="A5" t="inlineStr"><is><t>QT114202</t></is></c>
<c r="B5" t="s"><v>8</v></c><c r="C5" t="str"><v>115/1/15</v></c></row>
<row r="6"><c r="D6" t="s"><v>9</v></c></row>
</sheetData></worksheet>"""

SHARED_STRINGS = [
    "委託案件追蹤表", "案件編號", "客戶名稱", "預計完成日", "案件類型",
    "QT114201", "宏碩生技", "藥理試驗", "光宇製藥", "以上為本月新增",
]


def build_xlsx(sheet_xml=SHEET_XML, strings=SHARED_STRINGS, sheet_name="案件清單"):
    """組出一個最小但合法的 .xlsx，用來測試讀取器。"""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            f'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
            f'/package/2006/content-types"><Default Extension="rels" ContentType='
            f'"application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            f'<?xml version="1.0"?><Relationships xmlns="{pkg}"><Relationship Id="rId1"'
            f' Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            f'<?xml version="1.0"?><workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>'
            f'<sheet name="{sheet_name}" sheetId="1" r:id="rId1"/>'
            f'<sheet name="說明" sheetId="2" r:id="rId2"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<?xml version="1.0"?><Relationships xmlns="{pkg}">'
            f'<Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId2" Type="{rel}/worksheet" Target="worksheets/sheet2.xml"/>'
            f'<Relationship Id="rId3" Type="{rel}/styles" Target="styles.xml"/>'
            f'<Relationship Id="rId4" Type="{rel}/sharedStrings" Target="sharedStrings.xml"/>'
            f"</Relationships>",
        )
        items = "".join(f"<si><t>{text}</t></si>" for text in strings)
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<?xml version="1.0"?><sst xmlns="{ns}" count="{len(strings)}">{items}</sst>',
        )
        archive.writestr(
            "xl/styles.xml",
            f'<?xml version="1.0"?><styleSheet xmlns="{ns}"><cellXfs count="2">'
            f'<xf numFmtId="0"/><xf numFmtId="14" applyNumberFormat="1"/>'
            f"</cellXfs></styleSheet>",
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        archive.writestr(
            "xl/worksheets/sheet2.xml",
            f'<?xml version="1.0"?><worksheet xmlns="{ns}"><sheetData><row r="1">'
            f'<c r="A1" t="inlineStr"><is><t>使用說明</t></is></c></row></sheetData></worksheet>',
        )
    return buffer.getvalue()


class XlsxReaderTests(unittest.TestCase):
    def test_reads_shared_strings_and_sparse_cells(self):
        name, names, rows = xlsx_reader.read_rows(build_xlsx())
        self.assertEqual(name, "案件清單")
        self.assertEqual(names, ["案件清單", "說明"])
        self.assertEqual(rows[0], ["委託案件追蹤表"])
        # 第 2 列在 Excel 是空白列，要保留，列號才對得上
        self.assertEqual(rows[1], [])
        # D 欄在表頭是空的，讀出來要保留位置，欄位才不會錯位
        self.assertEqual(rows[2], ["案件編號", "客戶名稱", "預計完成日", "", "案件類型"])

    def test_date_styled_cell_becomes_iso_date(self):
        _, _, rows = xlsx_reader.read_rows(build_xlsx())
        self.assertEqual(rows[3][2], "2026-10-15")

    def test_inline_and_formula_strings(self):
        _, _, rows = xlsx_reader.read_rows(build_xlsx())
        self.assertEqual(rows[4][0], "QT114202")
        self.assertEqual(rows[4][2], "115/1/15")

    def test_named_sheet_and_missing_sheet(self):
        name, _, rows = xlsx_reader.read_rows(build_xlsx(), sheet="說明")
        self.assertEqual(name, "說明")
        self.assertEqual(rows[0], ["使用說明"])
        with self.assertRaises(xlsx_reader.SheetNotFound):
            xlsx_reader.read_rows(build_xlsx(), sheet="不存在")

    def test_not_a_zip_file(self):
        with self.assertRaises(ValueError):
            xlsx_reader.read_rows(b"this is not a spreadsheet")

    def test_reads_files_this_project_exports(self):
        raw = export.to_xlsx([models.decorate(sample(), TODAY)])
        _, _, rows = xlsx_reader.read_rows(raw)
        self.assertEqual(rows[0][0], "案件編號")
        self.assertEqual(rows[1][0], "QT114001")
        self.assertEqual(rows[1][7], "2026-09-20")  # 到期日欄還原成日期


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


class CaseNoRuleTests(unittest.TestCase):
    def test_prefix_required(self):
        with self.assertRaises(models.ValidationError) as ctx:
            models.validate(sample(case_no="A-1141101-R01"))
        self.assertIn("QT", ctx.exception.errors["case_no"])

    def test_prefix_normalised_to_uppercase(self):
        self.assertEqual(models.validate(sample(case_no="qt114001"))["case_no"], "QT114001")

    def test_prefix_enforced_on_partial_update_too(self):
        with self.assertRaises(models.ValidationError):
            models.validate({"case_no": "A-1141101-R01"}, partial=True)

    def test_contract_and_study_numbers_are_free_text(self):
        clean = models.validate(sample(contract_no="C-114-021 ", study_no="TMT-114-003"))
        self.assertEqual(clean["contract_no"], "C-114-021")
        self.assertEqual(clean["study_no"], "TMT-114-003")

    def test_contract_and_study_numbers_may_be_blank(self):
        clean = models.validate(sample(contract_no="", study_no=""))
        self.assertEqual((clean["contract_no"], clean["study_no"]), ("", ""))


class MigrationTests(unittest.TestCase):
    """v1（無合約／研究編號）的資料庫要能就地升級，舊資料不受影響。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "v1.db")
        db.close_thread_connection()

    def tearDown(self):
        db.close_thread_connection()
        self.tmp.cleanup()

    def _build_v1(self):
        import sqlite3
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_no TEXT NOT NULL COLLATE NOCASE,
                client TEXT NOT NULL DEFAULT '',
                case_type TEXT NOT NULL, stage TEXT NOT NULL,
                next_milestone TEXT NOT NULL DEFAULT '',
                due_date TEXT NOT NULL DEFAULT '',
                owner TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '', rev INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL, updated_by TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE case_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL,
                case_no TEXT NOT NULL, action TEXT NOT NULL,
                field TEXT NOT NULL DEFAULT '', old_value TEXT NOT NULL DEFAULT '',
                new_value TEXT NOT NULL DEFAULT '', operator TEXT NOT NULL DEFAULT '',
                changed_at TEXT NOT NULL
            );
            INSERT INTO cases (case_no, client, case_type, stage, status,
                               created_at, updated_at)
            VALUES ('QT114001', '宏碩生技', 'GLP 研究', '試驗執行中', '進行中',
                    '2026-01-01T09:00:00', '2026-01-01T09:00:00');
            PRAGMA user_version=1;
            """
        )
        conn.commit()
        conn.close()

    def test_upgrade_adds_columns_and_keeps_rows(self):
        self._build_v1()
        db.configure(self.path)
        conn = db.connect()
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(cases)")}
        self.assertIn("contract_no", columns)
        self.assertIn("study_no", columns)
        existing = db.get_by_case_no(conn, "QT114001")
        self.assertEqual(existing["contract_no"], "")

    def test_existing_rows_take_the_new_fields(self):
        self._build_v1()
        db.configure(self.path)
        conn = db.connect()
        existing = db.get_by_case_no(conn, "QT114001")
        updated = db.update_case(
            conn, existing["id"], {"contract_no": "C-114-021", "study_no": "TMT-114-003"},
            "測試員",
        )
        self.assertEqual(updated["contract_no"], "C-114-021")
        self.assertEqual(updated["study_no"], "TMT-114-003")


class CaseTypeTests(unittest.TestCase):
    def test_new_types_are_available(self):
        self.assertIn("藥理試驗", models.CASE_TYPES)
        self.assertIn("Pilot study", models.CASE_TYPES)

    def test_new_types_pass_validation(self):
        for case_type in ("藥理試驗", "Pilot study"):
            with self.subTest(case_type=case_type):
                clean = models.validate(sample(case_type=case_type))
                self.assertEqual(clean["case_type"], case_type)


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
        self.assertEqual(fetched["case_no"], "QT114001")

    def test_duplicate_case_no_rejected_case_insensitively(self):
        db.create_case(self.conn, sample())
        with self.assertRaises(db.DuplicateCaseNo):
            db.create_case(self.conn, sample(case_no="qt114001"))

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

    def test_update_rejects_non_qt_case_no(self):
        created = db.create_case(self.conn, sample())
        with self.assertRaises(models.ValidationError):
            db.update_case(self.conn, created["id"], {"case_no": "A-1141101-R01"})
        self.assertEqual(db.get_case(self.conn, created["id"])["case_no"], "QT114001")

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
        db.create_case(self.conn, sample(case_no="QT114010", client="宏碩生技", due_date="2026-09-01"))
        db.create_case(
            self.conn,
            sample(case_no="QT114011", client="光宇製藥", status="已結案",
                   case_type="稽核", due_date="2026-08-01"),
        )
        db.create_case(
            self.conn,
            sample(case_no="QT114012", client="英傑生醫", due_date="", owner="林郁涵"),
        )

    def test_search_matches_case_no_or_client(self):
        self.assertEqual(len(db.list_cases(self.conn, {"q": "QT114011"})), 1)
        self.assertEqual(len(db.list_cases(self.conn, {"q": "光宇"})), 1)

    def test_status_and_type_filters(self):
        self.assertEqual(len(db.list_cases(self.conn, {"status": "已結案"})), 1)
        self.assertEqual(len(db.list_cases(self.conn, {"case_type": "稽核"})), 1)

    def test_open_only_and_overdue_only(self):
        self.assertEqual(len(db.list_cases(self.conn, {"open_only": True})), 2)
        overdue = db.list_cases(self.conn, {"overdue_only": True}, today=TODAY)
        self.assertEqual([c["case_no"] for c in overdue], ["QT114010"])

    def test_default_order_puts_undated_and_closed_last(self):
        order = [c["case_no"] for c in db.list_cases(self.conn, {}, today=TODAY)]
        self.assertEqual(order, ["QT114010", "QT114012", "QT114011"])

    def test_explicit_sort(self):
        order = [
            c["case_no"]
            for c in db.list_cases(self.conn, {"sort": "case_no", "dir": "desc"})
        ]
        self.assertEqual(order, ["QT114012", "QT114011", "QT114010"])

    def test_search_matches_contract_and_study_numbers(self):
        self.assertEqual(len(db.list_cases(self.conn, {"q": "C-114-021"})), 3)
        self.assertEqual(len(db.list_cases(self.conn, {"q": "TMT-114-003"})), 3)

    def test_contract_filter_groups_one_contract(self):
        db.update_case(self.conn, db.get_by_case_no(self.conn, "QT114011")["id"],
                       {"contract_no": "C-114-099"})
        self.assertEqual(len(db.list_cases(self.conn, {"contract_no": "C-114-099"})), 1)

    def test_duplicate_contract_numbers_allowed(self):
        rows = db.list_cases(self.conn, {"contract_no": "C-114-021"})
        self.assertEqual(len(rows), 3)  # 一約多案

    def test_distinct_owners(self):
        self.assertIn("林郁涵", db.distinct_values(self.conn, "owner"))


class ReportTests(TempDbTestCase):
    def setUp(self):
        super().setUp()
        day = lambda n: (TODAY + timedelta(days=n)).isoformat()  # noqa: E731
        db.create_case(self.conn, sample(case_no="QT114020", due_date=day(-4)))
        db.create_case(self.conn, sample(case_no="QT114021", due_date=day(3)))
        db.create_case(self.conn, sample(case_no="QT114022", due_date=day(30)))
        db.create_case(self.conn, sample(case_no="QT114023", due_date=""))
        db.create_case(self.conn, sample(case_no="QT114024", due_date=day(-9), status="已結案"))

    def test_buckets(self):
        summary = report.weekly_summary(self.conn, TODAY, 7)
        self.assertEqual([c["case_no"] for c in summary["overdue"]], ["QT114020"])
        self.assertEqual([c["case_no"] for c in summary["upcoming"]], ["QT114021"])
        self.assertEqual([c["case_no"] for c in summary["undated"]], ["QT114023"])
        self.assertEqual(summary["totals"]["active"], 4)

    def test_wider_window_includes_more(self):
        summary = report.weekly_summary(self.conn, TODAY, 30)
        self.assertEqual(
            [c["case_no"] for c in summary["upcoming"]], ["QT114021", "QT114022"]
        )

    def test_text_and_exports(self):
        summary = report.weekly_summary(self.conn, TODAY, 7)
        text = report.to_text(summary)
        self.assertIn("QT114020", text)
        self.assertIn("已逾期", text)
        self.assertTrue(report.to_csv(summary).startswith(b"\xef\xbb\xbf"))
        self.assertTrue(report.to_xlsx(summary).startswith(b"PK"))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.items = [models.decorate(sample(), TODAY), models.decorate(sample(case_no="QT114002", notes="含 < > & 特殊字元"), TODAY)]

    def test_csv_has_bom_and_headers(self):
        raw = export.to_csv(self.items)
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8-sig")
        self.assertTrue(text.startswith("案件編號,合約編號,研究編號,客戶名稱"))
        self.assertIn("QT114001", text)
        self.assertIn("TMT-114-003", text)

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


class TemplateTests(TempDbTestCase):
    """匯入範本必須能被本系統自己讀回來、而且範例資料要是合法的。"""

    def setUp(self):
        super().setUp()
        self.raw = template.build()

    def test_two_sheets_and_header_on_first_row(self):
        name, names, rows = xlsx_reader.read_rows(self.raw)
        self.assertEqual(names, [template.SHEET_DATA, template.SHEET_HELP])
        self.assertEqual(name, template.SHEET_DATA)
        self.assertEqual(rows[0], [label for _, label in models.FIELD_LABELS])

    def test_data_sheet_is_empty_so_nothing_is_imported_by_accident(self):
        items, info = importer.read_items(self.raw, "範本.xlsx")
        self.assertEqual(info["header_row"], 1)
        self.assertEqual(items, [])
        self.assertEqual(importer.plan(self.conn, items)["totals"]["total"], 0)

    def test_every_field_is_recognised_by_the_importer(self):
        _, info = importer.read_items(self.raw, "範本.xlsx")
        self.assertEqual(
            [column["field"] for column in info["columns"]],
            models.EDITABLE_FIELDS,
        )
        self.assertEqual(info["ignored_columns"], [])

    def test_examples_in_the_help_sheet_are_valid_cases(self):
        items, _ = importer.read_items(self.raw, "範本.xlsx", sheet=template.SHEET_HELP)
        self.assertEqual(len(items), len(template.EXAMPLE_ROWS))
        created, updated, skipped, errors = importer.commit(self.conn, items, "測試員")
        self.assertEqual((created, updated, skipped, errors), (3, 0, 0, []))

    def test_dropdown_options_match_the_current_field_definitions(self):
        sheet = zipfile.ZipFile(io.BytesIO(self.raw)).read(
            "xl/worksheets/sheet1.xml"
        ).decode("utf-8")
        for options in (models.CASE_TYPES, models.STAGES, models.STATUSES):
            self.assertIn(",".join(options), sheet)

    def test_every_part_is_valid_xml(self):
        with zipfile.ZipFile(io.BytesIO(self.raw)) as archive:
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                ElementTree.fromstring(archive.read(name))


class ImportTests(TempDbTestCase):
    def _write(self, text, name="in.csv"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(text)
        return path

    # ---------------- CSV ----------------

    def test_import_chinese_headers(self):
        path = self._write(
            "案件編號,合約編號,研究編號,客戶名稱,案件類型,目前階段,到期日,負責人,狀態\r\n"
            "QT114031,C-114-021,TMT-114-003,宏碩生技,GLP 研究,試驗執行中,2026/9/20,陳彥廷,進行中\r\n"
            "QT114032,C-114-021,,光宇製藥,稽核,QA 審查,20261001,王孟儒,需留意\r\n"
        )
        created, updated, skipped, errors, _ = importer.import_file(self.conn, path)
        self.assertEqual((created, updated, skipped, errors), (2, 0, 0, []))
        row = db.get_by_case_no(self.conn, "QT114031")
        self.assertEqual(row["due_date"], "2026-09-20")
        self.assertEqual(row["contract_no"], "C-114-021")
        self.assertEqual(row["study_no"], "TMT-114-003")
        self.assertEqual(db.get_by_case_no(self.conn, "QT114032")["due_date"], "2026-10-01")

    def test_reimport_updates_existing(self):
        path = self._write("案件編號,負責人\r\nQT114031,甲\r\n")
        importer.import_file(self.conn, path)
        path2 = self._write("案件編號,負責人\r\nQT114031,乙\r\n", "in2.csv")
        created, updated, _, _, _ = importer.import_file(self.conn, path2)
        self.assertEqual((created, updated), (0, 1))
        self.assertEqual(db.get_by_case_no(self.conn, "QT114031")["owner"], "乙")

    def test_no_update_flag_skips_existing(self):
        path = self._write("案件編號,負責人\r\nQT114031,甲\r\n")
        importer.import_file(self.conn, path)
        path2 = self._write("案件編號,負責人\r\nQT114031,乙\r\n", "in2.csv")
        created, updated, skipped, _, _ = importer.import_file(
            self.conn, path2, update_existing=False
        )
        self.assertEqual((created, updated, skipped), (0, 0, 1))
        self.assertEqual(db.get_by_case_no(self.conn, "QT114031")["owner"], "甲")

    def test_bad_rows_are_reported_not_fatal(self):
        path = self._write(
            "案件編號,案件類型\r\nQT114041,稽核\r\nQT114042,不存在的類型\r\n"
        )
        created, _, _, errors, _ = importer.import_file(self.conn, path)
        self.assertEqual(created, 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("QT114042", errors[0])

    def test_missing_case_no_column(self):
        path = self._write("客戶名稱\r\n宏碩生技\r\n")
        with self.assertRaises(ValueError):
            importer.import_file(self.conn, path)

    # ---------------- Excel ----------------

    def test_import_xlsx_with_title_rows_and_alias_headers(self):
        items, info = importer.read_items(build_xlsx(), "模板.xlsx")
        self.assertEqual(info["sheet"], "案件清單")
        self.assertEqual(info["header_row"], 3)  # 表頭不在第一列
        self.assertEqual(
            {c["label"]: c["field"] for c in info["columns"]},
            {"案件編號": "case_no", "客戶名稱": "client",
             "預計完成日": "due_date", "案件類型": "case_type"},
        )
        created, updated, _, errors, = importer.commit(self.conn, items, "測試員")[:4]
        self.assertEqual((created, updated, errors), (2, 0, []))
        self.assertEqual(db.get_by_case_no(self.conn, "QT114201")["due_date"], "2026-10-15")
        self.assertEqual(db.get_by_case_no(self.conn, "QT114201")["case_type"], "藥理試驗")

    def test_minguo_date_is_converted(self):
        items, _ = importer.read_items(build_xlsx(), "模板.xlsx")
        importer.commit(self.conn, items, "測試員")
        # 115/1/15 為民國年寫法
        self.assertEqual(db.get_by_case_no(self.conn, "QT114202")["due_date"], "2026-01-15")

    def test_trailing_note_rows_are_ignored(self):
        items, _ = importer.read_items(build_xlsx(), "模板.xlsx")
        self.assertEqual([i["case_no"] for i in items], ["QT114201", "QT114202"])

    def test_missing_fields_get_defaults(self):
        items, _ = importer.read_items(build_xlsx(), "模板.xlsx")
        importer.commit(self.conn, items, "測試員")
        row = db.get_by_case_no(self.conn, "QT114202")
        self.assertEqual(row["stage"], models.STAGES[0])
        self.assertEqual(row["status"], models.STATUSES[0])

    def test_named_sheet_without_table_is_reported(self):
        with self.assertRaises(ValueError):
            importer.read_items(build_xlsx(), "模板.xlsx", sheet="說明")

    def test_xls_is_rejected_with_guidance(self):
        with self.assertRaises(ValueError) as ctx:
            importer.load_rows(b"anything", "舊檔.xls")
        self.assertIn("另存新檔", str(ctx.exception))

    # ---------------- 預覽 ----------------

    def test_plan_reports_create_update_and_errors(self):
        db.create_case(self.conn, sample(case_no="QT114201"))
        path = self._write(
            "案件編號,案件類型\r\n"
            "QT114201,稽核\r\n"      # 已存在 -> 更新
            "QT114301,Pilot study\r\n"  # 新增
            "QT114301,稽核\r\n"      # 檔案內重複
            "AB999,稽核\r\n"         # 編號不合規
        )
        items, _ = importer.read_items(path, "in.csv")
        result = importer.plan(self.conn, items)
        self.assertEqual(result["totals"],
                         {"create": 1, "update": 1, "error": 2, "total": 4})
        actions = [row["action"] for row in result["rows"]]
        self.assertEqual(actions, ["update", "create", "error", "error"])
        self.assertIn("已有相同案件編號", result["rows"][2]["message"])

    def test_plan_does_not_write(self):
        path = self._write("案件編號\r\nQT114301\r\n")
        items, _ = importer.read_items(path, "in.csv")
        importer.plan(self.conn, items)
        self.assertIsNone(db.get_by_case_no(self.conn, "QT114301"))


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
        self.assertIn("案件追蹤系統", body.decode("utf-8"))
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

        status, listing = self.json_request("GET", "/api/cases?q=QT1140")
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

    def _upload(self, path, data):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("X-Operator", "%E6%B8%AC%E8%A9%A6%E5%93%A1")
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_import_preview_then_commit(self):
        data = build_xlsx()
        status, preview = self._upload("/api/import/preview?filename=%E6%A8%A1%E6%9D%BF.xlsx", data)
        self.assertEqual(status, 200)
        self.assertEqual(preview["totals"], {"create": 2, "update": 0, "error": 0, "total": 2})
        self.assertEqual(preview["info"]["header_row"], 3)
        self.assertEqual(preview["info"]["sheet_names"], ["案件清單", "說明"])
        # 預覽不可以寫入資料庫
        self.assertEqual(self.json_request("GET", "/api/cases")[1]["count"], 0)

        status, result = self._upload("/api/import/commit?filename=%E6%A8%A1%E6%9D%BF.xlsx", data)
        self.assertEqual(status, 200)
        self.assertEqual((result["created"], result["updated"], result["errors"]), (2, 0, []))
        self.assertEqual(self.json_request("GET", "/api/cases")[1]["count"], 2)

        # 再匯入同一份檔案：不會重複新增，兩筆都走更新
        status, again = self._upload("/api/import/commit?filename=%E6%A8%A1%E6%9D%BF.xlsx", data)
        self.assertEqual((again["created"], again["updated"]), (0, 2))
        self.assertEqual(self.json_request("GET", "/api/cases")[1]["count"], 2)

    def test_import_commit_can_skip_existing(self):
        data = build_xlsx()
        self._upload("/api/import/commit?filename=a.xlsx", data)
        status, result = self._upload(
            "/api/import/commit?filename=a.xlsx&update_existing=0", data
        )
        self.assertEqual((result["created"], result["updated"], result["skipped"]), (0, 0, 2))

    def test_import_rejects_unreadable_file(self):
        status, payload = self._upload("/api/import/preview?filename=x.xlsx", b"not a workbook")
        self.assertEqual(status, 400)
        self.assertIn("Excel", payload["error"])

    def test_import_reports_missing_header(self):
        csv_bytes = "客戶名稱,負責人\r\n宏碩生技,陳彥廷\r\n".encode("utf-8-sig")
        status, payload = self._upload("/api/import/preview?filename=x.csv", csv_bytes)
        self.assertEqual(status, 400)
        self.assertIn("案件編號", payload["error"])

    def test_import_without_body_is_rejected(self):
        status, payload = self._upload("/api/import/preview?filename=x.csv", b"")
        self.assertEqual(status, 400)

    def test_template_download(self):
        status, body, headers = self.request("GET", "/export/template.xlsx")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"PK"))
        self.assertIn("spreadsheetml", headers["Content-Type"])
        _, names, rows = xlsx_reader.read_rows(body)
        self.assertEqual(names, [template.SHEET_DATA, template.SHEET_HELP])
        self.assertEqual(rows[0][0], "案件編號")

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
