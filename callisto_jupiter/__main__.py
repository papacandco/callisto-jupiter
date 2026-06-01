"""CLI entry point for the callisto-jupiter exporter.

Usage:
    callisto-jupiter            # run the daemon loop
    callisto-jupiter --once     # collect + push a single cycle, then exit
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .agent import Agent, run
from .config import ConfigError, load_config


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="callisto-jupiter", description=__doc__)
    parser.add_argument("--once", action="store_true", help="run a single collect+push cycle and exit")
    parser.add_argument("--version", action="version", version=f"callisto-jupiter {__version__}")
    args = parser.parse_args(argv)

    _setup_logging()

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 1

    if args.once:
        from .collectors import prime_cpu

        prime_cpu()
        agent = Agent(config)
        return 0 if agent.run_once() else 2

    run(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
