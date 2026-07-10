from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from india_market_news.fetcher import NewsFetcher
from india_market_news.pipeline import run_pipeline
from india_market_news.supabase_store import SupabaseStore, snapshots_to_items
from india_market_news.tickers import load_ticker_symbols

DEFAULT_TICKER_CSV = Path(__file__).resolve().parents[2] / "data" / "EQUITY_L.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch NSE news from Zerodha Markets and store in Supabase"
    )
    parser.add_argument(
        "--ticker-csv",
        type=Path,
        default=DEFAULT_TICKER_CSV,
        help="Path to NSE EQUITY_L.csv",
    )
    parser.add_argument(
        "--series",
        default="EQ",
        help="Filter by series column (default: EQ). Use ALL for every row.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel fetch workers (default: 8)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Tickers per batch (default: 100)",
    )
    parser.add_argument(
        "--batch-pause",
        type=float,
        default=20.0,
        help="Seconds to pause between batches (default: 20)",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.25,
        help="Minimum seconds between Zerodha requests (default: 0.25)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch only, do not write to Supabase",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit tickers for testing (0 = all)",
    )
    parser.add_argument(
        "--skip-corporate-actions",
        action="store_true",
        help="Skip corporate action upserts",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    series = None if args.series.upper() == "ALL" else args.series
    tickers = load_ticker_symbols(args.ticker_csv, series=series)
    if args.limit:
        tickers = tickers[: args.limit]

    if args.dry_run:
        snapshots = NewsFetcher(
            max_workers=args.workers,
            request_delay=args.request_delay,
        ).fetch_tickers(tickers)
        news, corp = snapshots_to_items(snapshots)
        payload = {
            "tickers": len(tickers),
            "ok": sum(1 for snapshot in snapshots if not snapshot.error),
            "news_seen": len(news),
            "corp_seen": len(corp),
        }
        print(json.dumps(payload, indent=2))
        return 0

    stats = run_pipeline(
        ticker_csv=args.ticker_csv,
        store=SupabaseStore.from_env(),
        include_corporate_actions=not args.skip_corporate_actions,
        max_workers=args.workers,
        batch_size=args.batch_size,
        batch_pause_seconds=args.batch_pause,
        request_delay=args.request_delay,
        series=series,
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
