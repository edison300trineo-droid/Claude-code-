"""週報：本週到期／已逾期案件摘要。"""

from datetime import date, timedelta

from . import db, export, models

REPORT_COLUMNS = [
    ("bucket", "分類"),
    ("case_no", "案件編號"),
    ("client", "客戶名稱"),
    ("case_type", "案件類型"),
    ("stage", "目前階段"),
    ("next_milestone", "下一個里程碑"),
    ("due_date", "到期日"),
    ("due_label", "期限狀態"),
    ("owner", "負責人"),
    ("status", "狀態"),
]

REPORT_WIDTHS = [12, 22, 22, 14, 16, 30, 12, 14, 12, 10]


def weekly_summary(conn, today=None, days=models.DUE_SOON_DAYS):
    """產生摘要：已逾期、指定天數內到期、未設定到期日的進行中案件。"""
    today = today or date.today()
    horizon = today + timedelta(days=days)

    active = db.list_cases(conn, {"open_only": True}, today=today)

    overdue = [c for c in active if c["due_state"] == "overdue"]
    overdue.sort(key=lambda c: (c["due_date"], c["case_no"]))

    upcoming = [
        c
        for c in active
        if c["due_state"] in ("due_soon", "scheduled")
        and c["due_date"]
        and c["due_date"] <= horizon.isoformat()
    ]
    upcoming.sort(key=lambda c: (c["due_date"], c["case_no"]))

    undated = [c for c in active if c["due_state"] == "none"]
    undated.sort(key=lambda c: c["case_no"])

    by_owner = {}
    for case in overdue + upcoming:
        owner = case["owner"] or "（未指派）"
        bucket = by_owner.setdefault(owner, {"overdue": 0, "upcoming": 0})
        bucket["overdue" if case["due_state"] == "overdue" else "upcoming"] += 1

    return {
        "generated_at": today.isoformat(),
        "window_days": days,
        "window_end": horizon.isoformat(),
        "overdue": overdue,
        "upcoming": upcoming,
        "undated": undated,
        "totals": {
            "overdue": len(overdue),
            "upcoming": len(upcoming),
            "undated": len(undated),
            "active": len(active),
        },
        "by_owner": [
            {"owner": owner, **counts}
            for owner, counts in sorted(
                by_owner.items(), key=lambda kv: (-kv[1]["overdue"], kv[0])
            )
        ],
        "status_counts": db.counts_by_status(conn),
    }


def flatten(summary):
    """把摘要攤平成可匯出的資料列。"""
    rows = []
    for case in summary["overdue"]:
        rows.append({**case, "bucket": "已逾期"})
    for case in summary["upcoming"]:
        rows.append({**case, "bucket": f"{summary['window_days']} 日內到期"})
    for case in summary["undated"]:
        rows.append({**case, "bucket": "未設定到期日"})
    return rows


def to_csv(summary):
    return export.to_csv(flatten(summary), REPORT_COLUMNS)


def to_xlsx(summary):
    return export.to_xlsx(
        flatten(summary),
        REPORT_COLUMNS,
        sheet_name=f"到期摘要 {summary['generated_at']}",
        widths=REPORT_WIDTHS,
    )


def to_text(summary):
    """終端機／郵件用的純文字摘要。"""
    lines = [
        f"案件到期摘要　基準日 {summary['generated_at']}"
        f"（涵蓋至 {summary['window_end']}）",
        "=" * 60,
        f"進行中案件 {summary['totals']['active']} 件："
        f"已逾期 {summary['totals']['overdue']}、"
        f"{summary['window_days']} 日內到期 {summary['totals']['upcoming']}、"
        f"未設定到期日 {summary['totals']['undated']}",
        "",
    ]

    def block(title, cases):
        lines.append(f"■ {title}（{len(cases)} 件）")
        if not cases:
            lines.append("　（無）")
        for case in cases:
            lines.append(
                f"　{case['case_no']:<22} {case['due_date'] or '－':<12}"
                f" {case['due_label']:<10} {case['owner'] or '未指派':<8}"
                f" {case['client']}／{case['next_milestone'] or case['stage']}"
            )
        lines.append("")

    block("已逾期", summary["overdue"])
    block(f"{summary['window_days']} 日內到期", summary["upcoming"])
    block("未設定到期日", summary["undated"])

    if summary["by_owner"]:
        lines.append("■ 依負責人")
        for entry in summary["by_owner"]:
            lines.append(
                f"　{entry['owner']:<10} 逾期 {entry['overdue']}　"
                f"即將到期 {entry['upcoming']}"
            )
    return "\n".join(lines)
