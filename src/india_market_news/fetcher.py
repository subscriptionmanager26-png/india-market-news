from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from india_market_news.models import TickerSnapshot
from india_market_news.zerodha import USER_AGENT, fetch_ticker

logger = logging.getLogger(__name__)


class RateLimiter:
    """Global throttle shared across worker threads."""

    def __init__(self, min_interval: float = 0.25):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self.min_interval - (now - self._last_at)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last_at = time.monotonic()


class NewsFetcher:
    def __init__(
        self,
        *,
        max_workers: int = 8,
        retry_count: int = 4,
        request_delay: float = 0.25,
    ):
        self.max_workers = max_workers
        self.retry_count = retry_count
        self.rate_limiter = RateLimiter(min_interval=request_delay)

    def fetch_tickers(
        self,
        tickers: list[str],
        *,
        exchange: str = "NSE",
    ) -> list[TickerSnapshot]:
        tickers = [ticker.strip().upper() for ticker in tickers if ticker.strip()]
        if not tickers:
            return []

        snapshots: list[TickerSnapshot] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(self._fetch_with_retry, ticker, exchange): ticker
                for ticker in tickers
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    snapshots.append(future.result())
                except Exception as exc:
                    logger.error("Fetch crashed for %s: %s", ticker, exc)
                    snapshots.append(
                        TickerSnapshot(
                            ticker=ticker,
                            exchange=exchange,
                            company_name=ticker,
                            url=f"https://zerodha.com/markets/stocks/{exchange}/{ticker}/",
                            tcm_id=None,
                            error=str(exc),
                        )
                    )

        order = {ticker: index for index, ticker in enumerate(tickers)}
        snapshots.sort(key=lambda item: order.get(item.ticker, len(order)))
        return snapshots

    def _fetch_with_retry(self, ticker: str, exchange: str) -> TickerSnapshot:
        last: TickerSnapshot | None = None
        for attempt in range(self.retry_count + 1):
            self.rate_limiter.wait()
            with httpx.Client(
                headers={"User-Agent": USER_AGENT},
                timeout=25.0,
                follow_redirects=True,
            ) as client:
                snapshot = fetch_ticker(ticker, exchange=exchange, client=client)

            if not snapshot.error or "429" not in snapshot.error:
                return snapshot

            last = snapshot
            sleep_for = min(2 ** attempt, 30) + random.uniform(0.5, 1.5)
            logger.warning(
                "Rate limited on %s (attempt %d/%d), sleeping %.1fs",
                ticker,
                attempt + 1,
                self.retry_count + 1,
                sleep_for,
            )
            time.sleep(sleep_for)

        return last or TickerSnapshot(
            ticker=ticker,
            exchange=exchange,
            company_name=ticker,
            url=f"https://zerodha.com/markets/stocks/{exchange}/{ticker}/",
            tcm_id=None,
            error="Rate limited after retries",
        )
