#!/usr/bin/env python3
"""Aggressive Zerodha rate-limit probe."""

from __future__ import annotations

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


def fetch_status(ticker: str, pacer: GlobalPacer | None = None) -> int:
    if pacer:
        pacer.wait()
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=25.0,
        follow_redirects=True,
    ) as client:
        return client.get(f"{BASE}/{ticker}/").status_code


def burst(tickers: list[str], workers: int, delay: float) -> dict:
    pacer = GlobalPacer(delay) if delay > 0 else None
    started = time.perf_counter()
    statuses: list[int] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_status, t, pacer) for t in tickers]
        for future in as_completed(futures):
            statuses.append(future.result())
    duration = time.perf_counter() - started
    counts: dict[int, int] = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return {
        "workers": workers,
        "delay": delay,
        "count": len(statuses),
        "duration_s": round(duration, 2),
        "req_per_s": round(len(statuses) / duration, 2),
        "status_counts": counts,
        "429": counts.get(429, 0),
    }


def main() -> None:
    tickers = load_ticker_symbols(ROOT / "data" / "EQUITY_L.csv", series="EQ")
    random.seed(7)
    pool = random.sample(tickers, 250)

    tests = [
        (40, 25, 0.0),
        (40, 8, 0.25),
        (40, 1, 0.0),
        (40, 1, 1.0),
        (40, 1, 2.0),
    ]

    for count, workers, delay in tests:
        sample = pool[:count]
        result = burst(sample, workers, delay)
        print(result)
        if result["429"]:
            print("  ^ first 429s observed")
        print("cooldown 30s\n")
        time.sleep(30)


if __name__ == "__main__":
    main()
