from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from app_config import load_config
from orchestrator import CatalogOrchestrator, RunOptions


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value cannot be negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract Safco Dental product families and variants.",
    )
    parser.add_argument("--config", default="config.yaml", help="YAML configuration path")
    parser.add_argument("--max-products", type=positive_int, help="global products this run")
    parser.add_argument("--resume", action="store_true", help="skip completed crawl states")
    parser.add_argument("--force-refresh", action="store_true", help="bypass fresh cache entries")
    parser.add_argument("--offline", action="store_true", help="read only cached responses")
    parser.add_argument("--no-llm", action="store_true", help="disable optional shadow extraction")
    parser.add_argument(
        "--shadow-sample",
        type=nonnegative_int,
        help="number of extracted products sampled by the optional LLM",
    )
    progress = parser.add_mutually_exclusive_group()
    progress.add_argument(
        "--progress",
        dest="progress",
        action="store_true",
        default=None,
        help="force the live progress view (default: on when stderr is a terminal)",
    )
    progress.add_argument(
        "--no-progress",
        dest="progress",
        action="store_false",
        help="disable the live progress view and log JSON events to stderr",
    )
    parser.add_argument(
        "--log-path",
        help="write JSON events to this file instead of stderr",
    )
    parser.add_argument(
        "--print-report",
        action="store_true",
        help="print the full run report JSON to stdout even in interactive runs",
    )
    return parser


async def run_from_args(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    options = RunOptions(
        max_products=args.max_products,
        resume=args.resume,
        force_refresh=args.force_refresh,
        offline=args.offline,
        no_llm=args.no_llm,
        shadow_sample=args.shadow_sample,
        progress=args.progress,
        log_path=args.log_path,
    )
    return await CatalogOrchestrator(config, options).run()


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.offline and args.force_refresh:
        build_parser().error("--offline and --force-refresh cannot be combined")
    try:
        report = asyncio.run(run_from_args(args))
    except KeyboardInterrupt:
        print("crawl interrupted; rerun with --resume", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    # An interactive run already showed its summary live; dumping the full report
    # would bury it. Redirected output keeps emitting JSON so pipes still work.
    interactive = args.progress if args.progress is not None else sys.stderr.isatty()
    if args.print_report or not interactive:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"run report: {report.get('outputs', {}).get('run_report', 'output/run_report.json')}")
    if report.get("status") == "completed":
        return 0
    return 2 if report.get("status") == "partial" else 1


if __name__ == "__main__":
    raise SystemExit(main())
