"""命令列介面。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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

    try:
        result = run_pipeline(
            config,
            output_name=None if args.command == "check" else getattr(args, "output_name", None),
        )
    except FileNotFoundError as exc:
        print(f"執行失敗：{exc}", file=sys.stderr)
        return 1

    print(f"研究編號：{config.study_id}")
    print(f"讀入 run 數：{result.run_count}")
    print(f"最終採用檢體數：{result.sample_count}")
    print(f"需人工複核列數：{result.manual_review_count}")
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
