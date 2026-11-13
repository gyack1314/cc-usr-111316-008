"""价格指数分析命令：读取、聚合、多边估计与滚窗接续。"""
from __future__ import annotations

import argparse
import json
import sys

from .exact import AnalysisError, ContractError
from .index import multilateral
from .panel import load_panel
from .report import analysis_view, frozen_view, transactions_view
from .rolling import rolling


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="投入价格多边指数与滚动接续")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("analyze", "输出交易聚合、共同篮子、覆盖率、边矩阵、指数与接续"),
        ("transactions", "输出交易聚合账与拒绝账"),
        ("index", "输出多边指数、边与残差"),
        ("rolling", "输出滚窗接续常数与冻结/修订序列"),
        ("inspect", "输出清单摘要与冻结版本"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("file", help="价格指数清单 JSON")
        sub.add_argument("--json", action="store_true", help="紧凑 JSON，便于程序读取")
        if name == "analyze":
            sub.add_argument("--no-rolling", action="store_true", help="跳过滚窗接续")
    return parser


def _dump(value: object, compact: bool) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=None if compact else 2,
        sort_keys=False,
        allow_nan=False,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        panel = load_panel(arguments.file)
        if arguments.command == "inspect":
            output = frozen_view(panel)
        elif arguments.command == "transactions":
            output = transactions_view(panel)
        elif arguments.command == "index":
            output = multilateral(panel)
        elif arguments.command == "rolling":
            output = rolling(panel)
        else:
            output = analysis_view(panel, include_rolling=not arguments.no_rolling)
    except (ContractError, AnalysisError, OSError) as exc:
        print(f"无法分析: {exc}", file=sys.stderr)
        return 2
    print(_dump(output, arguments.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
