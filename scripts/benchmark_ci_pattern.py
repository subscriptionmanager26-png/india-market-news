#!/usr/bin/env python3
"""Simulate CI pipeline batching and inspect rate-limit headers."""

from __future__ import annotations

import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from india_market_news.tickers import load_ticker_symbols
from india_market_news.zerodha import USER_AGENT

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


def fetch(ticker: str, pacer: GlobalPacer) -> tuple[int, dict[str, str]]:
    pacer.wait()
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=25.0,
        follow_redirects=True,
    ) as client:
        response = client.get(f"{BASE}/{ticker}/")
        headers = {
            k.lower(): v
            for k, v in response.headers.items()
            if k.lower()
            in {
                "retry-after",
                "x-ratelimit-limit",
                "x-ratelimit-remaining",
                "x-ratelimit-reset",
                "cf-ray",
                "server",
                "date",
            }
        }
        return response.status_code, headers


def run_batch(tickers: list[str], workers: int, delay: float) -> dict:
    pacer = GlobalPacer(delay)
    statuses: list[int] = []
    interesting_headers: dict[str, str] = {}
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, t, pacer) for t in tickers]
        for future in as_completed(futures):
            status, headers = future.result()
            statuses.append(status)
            if status == 429 or headers:
                interesting_headers = headers
    duration = time.perf_counter() - started
    counts: dict[int, int] = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return {
        "count": len(statuses),
        "duration_s": round(duration, 2),
        "status_counts": counts,
        "sample_headers": interesting_headers,
    }


def main() -> None:
    tickers = load_ticker_symbols(ROOT / "data" / "EQUITY_L.csv", series="EQ")
    random.seed(99)

    # Simulate full CI run: 21 batches of 100, 8 workers, 0.25s delay, 20s pause
    batch_size = 100
    workers = 8
    delay = 0.25
    pause = 20
    total_429 = 0
    started = time.perf_counter()

    for batch_idx in range(0, min(500, len(tickers)), batch_size):
        batch = tickers[batch_idx : batch_idx + batch_size]
        result = run_batch(batch, workers, delay)
        batch_429 = result["status_counts"].get(429, 0)
        total_429 += batch_429
        print(
            f"batch {batch_idx // batch_size + 1}: "
            f"ok={result['status_counts'].get(200, 0)} "
            f"429={batch_429} "
            f"duration={result['duration_s']}s "
            f"headers={result['sample_headers']}"
        )
        if batch_idx + batch_size < min(500, len(tickers)):
            time.sleep(pause)

    print(
        f"\nSimulated 500 tickers in {round(time.perf_counter() - started, 1)}s, "
        f"total 429={total_429}"
    )


if __name__ == "__main__":
    main()
