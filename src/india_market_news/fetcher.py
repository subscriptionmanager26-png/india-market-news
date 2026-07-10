from __future__ import annotations

import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from india_market_news.models import TickerSnapshot
from india_market_news.zerodha import USER_AGENT, fetch_ticker

logger = logging.getLogger(__name__)

_RETRY_AFTER_RE = re.compile(r"retry_after=(\d+(?:\.\d+)?)", re.IGNORECASE)


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


def _retry_sleep_seconds(error: str | None, attempt: int, cap: int = 60) -> float:
    if error:
        match = _RETRY_AFTER_RE.search(error)
        if match:
            return float(match.group(1))
    return min(2 ** attempt, cap) + random.uniform(0.5, 1.5)


class NewsFetcher:
    def __init__(
        self,
        *,
        max_workers: int = 8,
        retry_count: int = 4,
        request_delay: float = 0.25,
    ):
        self.max_workers = max(1, max_workers)
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

        if self.max_workers == 1:
            return self._fetch_sequential(tickers, exchange=exchange)

        snapshots: list[TickerSnapshot] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(self._fetch_with_retry, ticker, exchange, None): ticker
                for ticker in tickers
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    snapshots.append(future.result())
                except Exception as exc:
                    logger.error("Fetch crashed for %s: %s", ticker, exc)
                    snapshots.append(self._error_snapshot(ticker, exchange, str(exc)))

        order = {ticker: index for index, ticker in enumerate(tickers)}
        snapshots.sort(key=lambda item: order.get(item.ticker, len(order)))
        return snapshots

    def _fetch_sequential(
        self,
        tickers: list[str],
        *,
        exchange: str,
    ) -> list[TickerSnapshot]:
        snapshots: list[TickerSnapshot] = []
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=25.0,
            follow_redirects=True,
        ) as client:
            for ticker in tickers:
                snapshots.append(self._fetch_with_retry(ticker, exchange, client))
        return snapshots

    def _error_snapshot(
        self,
        ticker: str,
        exchange: str,
        error: str,
    ) -> TickerSnapshot:
        return TickerSnapshot(
            ticker=ticker,
            exchange=exchange,
            company_name=ticker,
            url=f"https://zerodha.com/markets/stocks/{exchange}/{ticker}/",
            tcm_id=None,
            error=error,
        )

    def _fetch_with_retry(
        self,
        ticker: str,
        exchange: str,
        client: httpx.Client | None,
    ) -> TickerSnapshot:
        last: TickerSnapshot | None = None
        for attempt in range(self.retry_count + 1):
            self.rate_limiter.wait()
            if client is None:
                with httpx.Client(
                    headers={"User-Agent": USER_AGENT},
                    timeout=25.0,
                    follow_redirects=True,
                ) as owned:
                    snapshot = fetch_ticker(ticker, exchange=exchange, client=owned)
            else:
                snapshot = fetch_ticker(ticker, exchange=exchange, client=client)

            if not snapshot.error or "429" not in snapshot.error:
                return snapshot

            last = snapshot
            sleep_for = _retry_sleep_seconds(snapshot.error, attempt)
            logger.warning(
                "Rate limited on %s (attempt %d/%d), sleeping %.1fs",
                ticker,
                attempt + 1,
                self.retry_count + 1,
                sleep_for,
            )
            time.sleep(sleep_for)

        return last or self._error_snapshot(
            ticker,
            exchange,
            "Rate limited after retries",
        )
