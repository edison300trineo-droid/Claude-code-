"""命令列介面。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .compare import (
    DEFAULT_SHEET,
    compare_tables,
    load_previous,
    write_comparison_report,
)
from .config import ConfigError, load_config
from .decisions import create_template
from .pipeline import run_pipeline


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c", "--config", required=True, type=Path,
        help="研究設定檔路徑 (例如 config/BD-TS-20260701.yaml)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qpcr-biod",
        description="Alu qPCR 生物分布數據統整 pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="讀取原始檔並產生統整表")
    _add_config_argument(run)
    run.add_argument("-o", "--output-name", help="輸出檔名（預設為 <研究編號>_統整表.xlsx）")

    init = subparsers.add_parser("init-decisions", help="產生空白的人工決策表")
    _add_config_argument(init)
    init.add_argument(
        "--overwrite", action="store_true",
        help="覆蓋已存在的決策表（會清掉同仁已填的內容，請謹慎使用）",
    )

    check = subparsers.add_parser("check", help="只做檢查，不輸出檔案")
    _add_config_argument(check)

    compare = subparsers.add_parser(
        "compare",
        help="把 pipeline 產出與既有統整表逐列對帳（導入驗收用）",
    )
    _add_config_argument(compare)
    compare.add_argument(
        "--against", required=True, type=Path,
        help="既有統整表的路徑（要對照的舊版 .xlsx）",
    )
    compare.add_argument(
        "--sheet", default=DEFAULT_SHEET,
        help=f"既有統整表中要對照的分頁名稱（預設 {DEFAULT_SHEET}）",
    )
    compare.add_argument(
        "--tolerance", type=float, default=1e-6,
        help="數值比對的相對容差（預設 1e-6）",
    )
    compare.add_argument(
        "--report", type=Path,
        help="把對照結果另存成 xlsx（預設寫到 output 資料夾）",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"設定檔錯誤：{exc}", file=sys.stderr)
        return 2

    if args.command == "init-decisions":
        path = create_template(config.path("decisions_file"), overwrite=args.overwrite)
        print(f"決策表：{path}")
        if not args.overwrite:
            print("（若檔案已存在則未覆蓋，同仁填寫的內容都保留）")
        return 0

    if args.command == "compare":
        return _run_compare(config, args)

    try:
        result = run_pipeline(
            config,
            output_name=getattr(args, "output_name", None),
            write_output=args.command != "check",
        )
    except FileNotFoundError as exc:
        print(f"執行失敗：{exc}", file=sys.stderr)
        return 1

    print(f"研究編號：{config.study_id}")
    print(f"讀入 run 數：{result.run_count}")
    print(f"最終採用檢體數：{result.sample_count}")
    print(f"需人工複核列數：{result.manual_review_count}")
    if result.output_path is None:
        print("輸出檔案：（check 模式，未寫入任何檔案）")
    else:
        print(f"輸出檔案：{result.output_path}")

    if result.warnings:
        print(f"\n警告 {len(result.warnings)} 則：")
        for index, warning in enumerate(result.warnings, start=1):
            print(f"  {index}. {warning}")

    if result.manual_review_count:
        print(
            f"\n有 {result.manual_review_count} 列標記為「請人工複核」"
            "（HIGHSD=Y、可定量、且無 rerun）。"
            "請在決策表「HIGHSD單孔採用」分頁指定採用孔位後重跑。"
        )
    return 0


def _run_compare(config, args) -> int:
    """對帳：pipeline 產出 vs. 既有統整表。"""
    try:
        previous = load_previous(args.against, args.sheet)
    except (FileNotFoundError, ValueError) as exc:
        print(f"對照失敗：{exc}", file=sys.stderr)
        return 1

    try:
        result = run_pipeline(config, write_output=False)
    except FileNotFoundError as exc:
        print(f"執行失敗：{exc}", file=sys.stderr)
        return 1

    comparison = compare_tables(previous, result.consolidated,
                                tolerance=args.tolerance)

    report_path = args.report or (
        config.path("output_dir") / f"{config.study_id}_對照報告.xlsx"
    )
    write_comparison_report(report_path, comparison)

    print(f"既有統整表：{args.against}（分頁「{args.sheet}」，{len(previous)} 列）")
    print(f"pipeline 產出：{len(result.consolidated)} 列")
    print(f"兩邊都有的列數：{comparison.matched}")
    print(f"有差異的列數：{comparison.differing_rows}")
    print(f"只在既有表：{len(comparison.only_in_old)} 列")
    print(f"只在 pipeline：{len(comparison.only_in_new)} 列")
    print(f"對照報告：{report_path}")

    for note in comparison.notes:
        print(f"  註：{note}")

    if comparison.is_clean:
        print("\n結果：完全一致。pipeline 重現了既有統整表。")
        return 0

    if comparison.values_match:
        # 分階段驗收時很常見：只放了部分原始檔，值卻是對的
        print(
            f"\n結果：兩邊都有的 {comparison.matched} 列，值完全一致。"
            "\n但兩邊涵蓋的列不同："
        )
        if len(comparison.only_in_old):
            print(f"  • 只在既有表：{len(comparison.only_in_old)} 列"
                  "（若 data/raw 尚未放齊全部原始檔，這是預期的）")
        if len(comparison.only_in_new):
            print(f"  • 只在 pipeline：{len(comparison.only_in_new)} 列"
                  "（既有表沒有這些列，請確認是不是新資料）")
        return 0

    print("\n結果：有值不一致。以下列出前 15 筆，完整清單請看對照報告。")
    for _, row in comparison.differences.head(15).iterrows():
        print(f"  {row['對照鍵']} | {row['欄位']}")
        print(f"      既有={row['既有統整表']}  pipeline={row['pipeline產出']}  {row['差異']}")

    print(
        "\n提醒：有差異不代表 pipeline 一定錯。舊表當初若有未記錄的人工調整，"
        "也會在這裡出現 —— 請逐筆確認差異的來源，確認後把該筆人工判斷補進決策表。"
    )
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
