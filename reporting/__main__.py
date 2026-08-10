"""Regenerate the dashboard from existing artifacts without running a crawl.

    python -m reporting
    python -m reporting --open
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from app_config import load_config

from .dashboard import write_dashboard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml", help="YAML configuration path")
    parser.add_argument("--output", help="destination HTML path")
    parser.add_argument("--open", action="store_true", help="open the page in a browser")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    sqlite_path = Path(config.storage.sqlite_path)
    if not sqlite_path.exists():
        print(f"no database at {sqlite_path}; run a crawl first", file=sys.stderr)
        return 1

    target = write_dashboard(
        args.output or config.output.dashboard_path,
        sqlite_path=sqlite_path,
        run_report_path=config.output.run_report_path,
        agreement_path=config.output.agreement_path,
        agreement_threshold=config.llm.agreement_threshold,
    )
    print(target)
    if args.open:
        webbrowser.open(target.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
