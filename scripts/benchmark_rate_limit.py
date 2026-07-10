#!/usr/bin/env python3
"""Probe Zerodha Markets rate limits with different pacing strategies."""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from india_market_news.tickers import load_ticker_symbols  # noqa: E402
from india_market_news.zerodha import USER_AGENT  # noqa: E402

BASE = "https://zerodha.com/markets/stocks/NSE"


class GlobalPacer:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self.min_interval - (now - self._last)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last = time.monotonic()


def fetch_one(ticker: str, pacer: GlobalPacer | None = None) -> tuple[str, int, float]:
    url = f"{BASE}/{ticker}/"
    started = time.perf_counter()
    if pacer:
        pacer.wait()
    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=25.0,
            follow_redirects=True,
        ) as client:
            response = client.get(url)
        elapsed = time.perf_counter() - started
        return ticker, response.status_code, elapsed
    except httpx.HTTPError as exc:
        elapsed = time.perf_counter() - started
        status = exc.response.status_code if exc.response else 0
        return ticker, status, elapsed


def run_sequential(tickers: list[str], delay: float) -> dict:
    results: list[tuple[str, int, float]] = []
    started = time.perf_counter()
    for ticker in tickers:
        if delay > 0:
            time.sleep(delay)
        results.append(fetch_one(ticker))
    duration = time.perf_counter() - started
    return summarize(results, duration, mode=f"sequential delay={delay}s")


def run_parallel(
    tickers: list[str],
    *,
    workers: int,
    request_delay: float,
) -> dict:
    pacer = GlobalPacer(request_delay) if request_delay > 0 else None
    results: list[tuple[str, int, float]] = []
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_one, ticker, pacer) for ticker in tickers]
        for future in as_completed(futures):
            results.append(future.result())
    duration = time.perf_counter() - started
    return summarize(
        results,
        duration,
        mode=f"parallel workers={workers} delay={request_delay}s",
    )


def summarize(
    results: list[tuple[str, int, float]],
    duration: float,
    *,
    mode: str,
) -> dict:
    by_status: dict[int, int] = {}
    latencies: list[float] = []
    failed: list[str] = []
    for ticker, status, elapsed in results:
        by_status[status] = by_status.get(status, 0) + 1
        latencies.append(elapsed)
        if status != 200:
            failed.append(f"{ticker}:{status}")

    return {
        "mode": mode,
        "count": len(results),
        "duration_s": round(duration, 2),
        "req_per_s": round(len(results) / duration, 2) if duration else 0,
        "status_counts": by_status,
        "ok": by_status.get(200, 0),
        "rate_limited": by_status.get(429, 0),
        "p50_ms": round(statistics.median(latencies) * 1000, 1),
        "failed_sample": failed[:8],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker-csv", type=Path, default=ROOT / "data" / "EQUITY_L.csv")
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    tickers = load_ticker_symbols(args.ticker_csv, series="EQ")
    random.seed(args.seed)
    sample = random.sample(tickers, min(args.count, len(tickers)))

    scenarios = [
        ("sequential", {"delay": 0.0}),
        ("sequential", {"delay": 1.0}),
        ("sequential", {"delay": 2.0}),
        ("parallel", {"workers": 8, "request_delay": 0.25}),
        ("parallel", {"workers": 1, "request_delay": 0.0}),
        ("parallel", {"workers": 1, "request_delay": 2.0}),
    ]

    print(f"Sample size: {len(sample)} tickers\n")
    for kind, params in scenarios:
        if kind == "sequential":
            result = run_sequential(sample[:30], **params)
        else:
            result = run_parallel(sample[:30], **params)
        print(result)
        # Cool down between scenarios to reset any sliding window limiter
        print("cooldown 10s...\n")
        time.sleep(10)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
