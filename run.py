#!/usr/bin/env python3
"""案件／專案追蹤系統 啟動程式。

用法：
    python3 run.py                       # 啟動伺服器（預設 http://0.0.0.0:8765）
    python3 run.py --port 9000           # 指定埠號
    python3 run.py report --days 14      # 在終端機列印到期／逾期摘要
    python3 run.py export 案件清單.xlsx    # 匯出全部案件（.xlsx 或 .csv）
    python3 run.py import 既有清單.xlsx    # 由現有 Excel／CSV 匯入
    python3 run.py import 清單.xlsx --dry-run   # 只試算不寫入
    python3 run.py seed-demo             # 寫入示範資料（僅限空資料庫）
"""

import argparse
import os
import socket
import sys
import threading
import webbrowser
from datetime import date, timedelta

from case_tracker import __version__, db, export, importer, models, report, server

DEFAULT_DB = os.environ.get(
    "CASE_TRACKER_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cases.db"),
)
DEFAULT_HOST = os.environ.get("CASE_TRACKER_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.environ.get("CASE_TRACKER_PORT", "8765"))


def lan_hint(host, port):
    if host not in ("0.0.0.0", "::"):
        return f"http://{host}:{port}/"
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.connect(("192.0.2.1", 9))  # TEST-NET，不會實際送出封包
        address = probe.getsockname()[0]
        probe.close()
    except OSError:
        address = socket.gethostbyname(socket.gethostname())
    return f"http://{address}:{port}/"


def cmd_serve(args):
    path = db.configure(args.db)
    httpd = server.serve(args.host, args.port, verbose=args.verbose)
    print(f"案件追蹤系統 v{__version__}")
    print(f"  資料庫　：{path}")
    print(f"  本機開啟：http://127.0.0.1:{args.port}/")
    if args.host in ("0.0.0.0", "::"):
        print(f"  同仁連線：{lan_hint(args.host, args.port)}")
        hostname = socket.gethostname()
        if hostname:
            # IP 可能隨網路重新分配而變動，電腦名稱通常比較穩定，適合當書籤。
            print(f"  　或用　：http://{hostname}:{args.port}/")
    print("  停止服務：關閉此視窗，或按 Ctrl+C")
    if args.open:
        threading.Timer(
            1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/")
        ).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


def cmd_report(args):
    db.configure(args.db)
    summary = report.weekly_summary(db.connect(), date.today(), args.days)
    if args.output:
        data = (
            report.to_xlsx(summary)
            if args.output.lower().endswith(".xlsx")
            else report.to_csv(summary)
            if args.output.lower().endswith(".csv")
            else report.to_text(summary).encode("utf-8-sig")
        )
        with open(args.output, "wb") as handle:
            handle.write(data)
        print(f"已寫入 {args.output}")
    else:
        print(report.to_text(summary))
    return 0


def cmd_export(args):
    db.configure(args.db)
    items = db.list_cases(db.connect(), {"open_only": args.open_only})
    lower = args.output.lower()
    if lower.endswith(".xlsx"):
        data = export.to_xlsx(items)
    elif lower.endswith(".csv"):
        data = export.to_csv(items)
    else:
        print("輸出檔名須為 .xlsx 或 .csv", file=sys.stderr)
        return 2
    with open(args.output, "wb") as handle:
        handle.write(data)
    print(f"已匯出 {len(items)} 筆案件至 {args.output}")
    return 0


def cmd_import(args):
    db.configure(args.db)
    conn = db.connect()
    try:
        items, info = importer.read_items(
            args.input, os.path.basename(args.input), args.sheet
        )
    except (ValueError, OSError) as exc:
        print(f"讀取失敗：{exc}", file=sys.stderr)
        return 2

    if info.get("sheet"):
        print(f"工作表：{info['sheet']}（檔案內有：{'、'.join(info['sheet_names'])}）")
    print(f"表頭在第 {info['header_row']} 列，對應到的欄位：")
    for column in info["columns"]:
        print(f"  {column['label']} → {dict(models.FIELD_LABELS)[column['field']]}")
    if info["ignored_columns"]:
        print(f"未使用的欄位：{'、'.join(info['ignored_columns'])}")

    if args.dry_run:
        result = importer.plan(conn, items)
        totals = result["totals"]
        print(
            f"\n試算結果（未寫入）：將新增 {totals['create']} 筆、"
            f"更新 {totals['update']} 筆、無法匯入 {totals['error']} 筆"
        )
        for row in result["rows"]:
            if row["action"] == "error":
                print(f"  第 {row['row']} 列（{row['case_no']}）：{row['message']}",
                      file=sys.stderr)
        return 0

    created, updated, skipped, errors = importer.commit(
        conn, items, args.operator, update_existing=not args.no_update
    )
    print(f"\n新增 {created} 筆、更新 {updated} 筆" + (f"、略過 {skipped} 筆" if skipped else "") + "。")
    if errors:
        print(f"有 {len(errors)} 列未匯入：", file=sys.stderr)
        for line in errors:
            print(f"  - {line}", file=sys.stderr)
    return 1 if errors else 0


DEMO_CASES = [
    # 案件編號, 合約編號, 研究編號, 客戶, 類型, 階段, 里程碑, 到期日偏移, 負責人, 狀態, 備註
    ("QT114001", "C-114-021", "TMT-114-003", "宏碩生技", "GLP 研究", "試驗執行中",
     "第 28 天中期報告", 5, "陳彥廷", "進行中",
     "SD 大鼠 28 天重複劑量毒性；動物房週報每週五更新。"),
    ("QT114002", "C-114-021", "TMT-114-004", "宏碩生技", "生物分析", "檢體分析中",
     "第二批檢體完成分析", 12, "林郁涵", "進行中",
     "與 QT114001 同一份合約，血漿樣本併批分析。"),
    ("QT114003", "C-114-018", "TMT-114-002", "光宇製藥", "方法確效", "QA 審查",
     "確效報告送 QA", -3, "林郁涵", "已延遲",
     "LC-MS/MS 血漿定量，選擇性再測一批。"),
    ("QT114004", "C-114-025", "", "誠泰藥業", "稽核", "報告草稿",
     "稽核報告初稿內部審閱", 2, "王孟儒", "需留意",
     "客戶要求同步提供 CAPA 建議。"),
    ("QT114005", "C-114-011", "TMT-114-001", "北辰醫材", "外包試驗支援", "客戶審閱",
     "客戶回覆意見", 20, "張佩妤", "進行中",
     "外包至合作機構執行生物相容性試驗。"),
    ("QT114006", "", "", "Meridian Bio", "BD 授權評估", "計畫書撰寫",
     "技術盡職調查會議", 9, "陳彥廷", "需留意",
     "NDA 已簽署，合約與研究編號待立案後補。"),
    ("QT113018", "C-113-047", "TMT-113-016", "宏碩生技", "GLP 研究", "結案",
     "—", -40, "王孟儒", "已結案",
     "正式報告已於 8 月發出，檢體依規定保存。"),
]


def cmd_seed_demo(args):
    db.configure(args.db)
    conn = db.connect()
    existing = conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"]
    if existing and not args.force:
        print(f"資料庫已有 {existing} 筆案件，未寫入示範資料（加 --force 可強制）。")
        return 0
    today = date.today()
    for (case_no, contract_no, study_no, client, ctype, stage, milestone, offset,
         owner, status, notes) in DEMO_CASES:
        if db.get_by_case_no(conn, case_no):
            continue
        db.create_case(
            conn,
            {
                "case_no": case_no,
                "contract_no": contract_no,
                "study_no": study_no,
                "client": client,
                "case_type": ctype,
                "stage": stage,
                "next_milestone": milestone,
                "due_date": (today + timedelta(days=offset)).isoformat(),
                "owner": owner,
                "status": status,
                "notes": notes,
            },
            operator="demo",
        )
    print(f"已寫入示範資料（共 {len(DEMO_CASES)} 筆）。")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Trifecta MedTek 案件／專案追蹤系統",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite 檔案路徑（預設 {DEFAULT_DB}）")
    parser.add_argument("--host", default=DEFAULT_HOST, help="監聽位址（預設 0.0.0.0，供內網同仁連線）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="監聽埠號（預設 8765）")
    parser.add_argument("--verbose", action="store_true", help="輸出每筆 HTTP 請求紀錄")
    parser.add_argument("--open", action="store_true", help="啟動後自動開啟瀏覽器")
    parser.set_defaults(func=cmd_serve)

    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="啟動網頁伺服器（預設動作）")
    p_serve.set_defaults(func=cmd_serve)

    p_report = sub.add_parser("report", help="列印或輸出到期／逾期摘要")
    p_report.add_argument("--days", type=int, default=models.DUE_SOON_DAYS, help="往後涵蓋天數（預設 7）")
    p_report.add_argument("-o", "--output", help="輸出檔案（.txt / .csv / .xlsx）")
    p_report.set_defaults(func=cmd_report)

    p_export = sub.add_parser("export", help="匯出全部案件")
    p_export.add_argument("output", help="輸出檔名（.xlsx 或 .csv）")
    p_export.add_argument("--open-only", action="store_true", help="只匯出未結案案件")
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser("import", help="由現有 Excel／CSV 匯入")
    p_import.add_argument("input", help="來源檔案（.xlsx／.xlsm／.csv）")
    p_import.add_argument("--sheet", help="指定工作表名稱（預設取第一個）")
    p_import.add_argument("--dry-run", action="store_true", help="只試算並列出結果，不寫入")
    p_import.add_argument("--operator", default="import", help="匯入者名稱，寫入異動紀錄")
    p_import.add_argument("--no-update", action="store_true", help="已存在的案件編號不覆寫")
    p_import.set_defaults(func=cmd_import)

    p_seed = sub.add_parser("seed-demo", help="寫入示範資料")
    p_seed.add_argument("--force", action="store_true", help="即使資料庫非空也寫入")
    p_seed.set_defaults(func=cmd_seed_demo)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
