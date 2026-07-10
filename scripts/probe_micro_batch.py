#!/usr/bin/env python3
"""Probe Zerodha with N parallel calls per micro-batch and pause between batches."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from india_market_news.fetcher import NewsFetcher  # noqa: E402
from india_market_news.tickers import load_ticker_symbols  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker-csv", type=Path, default=ROOT / "data" / "EQUITY_L.csv")
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--micro-batch-size", type=int, default=20)
    parser.add_argument("--micro-batch-pause", type=float, default=2.0)
    parser.add_argument("--request-delay", type=float, default=0.0)
    args = parser.parse_args()

    tickers = load_ticker_symbols(args.ticker_csv, series="EQ")[: args.count]
    fetcher = NewsFetcher(
        max_workers=args.workers,
        request_delay=args.request_delay,
        micro_batch_size=args.micro_batch_size,
        micro_batch_pause=args.micro_batch_pause,
    )

    started = time.perf_counter()
    snapshots = fetcher.fetch_tickers(tickers)
    duration = round(time.perf_counter() - started, 1)

    ok = sum(1 for snapshot in snapshots if not snapshot.error)
    rate_limited = sum(
        1 for snapshot in snapshots if snapshot.error and "429" in snapshot.error
    )
    other_failed = len(snapshots) - ok - rate_limited

    result = {
        "tickers": len(tickers),
        "ok": ok,
        "rate_limited": rate_limited,
        "other_failed": other_failed,
        "duration_seconds": duration,
        "workers": args.workers,
        "micro_batch_size": args.micro_batch_size,
        "micro_batch_pause": args.micro_batch_pause,
        "request_delay": args.request_delay,
        "failed_sample": [
            {"ticker": snapshot.ticker, "error": snapshot.error}
            for snapshot in snapshots
            if snapshot.error
        ][:10],
    }
    print(json.dumps(result, indent=2))
    return 0 if rate_limited == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
