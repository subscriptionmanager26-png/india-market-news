from __future__ import annotations

import logging
import time
from pathlib import Path

from india_market_news.fetcher import NewsFetcher
from india_market_news.supabase_store import SupabaseStore, snapshots_to_items
from india_market_news.tickers import load_tickers_from_csv

logger = logging.getLogger(__name__)


def _process_batch(
    *,
    store: SupabaseStore,
    fetcher: NewsFetcher,
    batch: list[str],
    include_corporate_actions: bool,
    stats: dict,
) -> list[str]:
    snapshots = fetcher.fetch_tickers(batch)
    ok = sum(1 for snapshot in snapshots if not snapshot.error)
    stats["tickers_ok"] += ok
    stats["tickers_failed"] += len(snapshots) - ok

    news_items, corp_items = snapshots_to_items(snapshots)
    stats["news_seen"] += len(news_items)

    inserted, skipped = store.upsert_news(news_items)
    stats["news_inserted"] += inserted
    stats["news_skipped"] += skipped

    if include_corporate_actions:
        stats["corp_seen"] += len(corp_items)
        corp_inserted, corp_skipped = store.upsert_corporate_actions(corp_items)
        stats["corp_inserted"] += corp_inserted
        stats["corp_skipped"] += corp_skipped

    failed = [snapshot.ticker for snapshot in snapshots if snapshot.error]
    return failed


def run_pipeline(
    *,
    ticker_csv: Path,
    store: SupabaseStore,
    run_type: str = "scheduled",
    include_corporate_actions: bool = True,
    max_workers: int = 8,
    batch_size: int = 100,
    batch_pause_seconds: float = 20.0,
    request_delay: float = 0.25,
    micro_batch_size: int = 0,
    micro_batch_pause: float = 2.0,
    series: str | None = "EQ",
) -> dict:
    ticker_rows = load_tickers_from_csv(ticker_csv, series=series)
    tickers = [row["nse_symbol"] for row in ticker_rows]

    run_id = store.start_fetch_run(run_type)
    started = time.perf_counter()

    stats = {
        "tickers_total": len(tickers),
        "tickers_ok": 0,
        "tickers_failed": 0,
        "news_seen": 0,
        "news_inserted": 0,
        "news_skipped": 0,
        "corp_seen": 0,
        "corp_inserted": 0,
        "corp_skipped": 0,
        "retention_deleted": 0,
    }

    try:
        store.sync_tickers(ticker_rows)
        fetcher = NewsFetcher(
            max_workers=max_workers,
            request_delay=request_delay,
            micro_batch_size=micro_batch_size,
            micro_batch_pause=micro_batch_pause,
        )
        failed_tickers: list[str] = []

        batches = [
            tickers[offset : offset + batch_size]
            for offset in range(0, len(tickers), batch_size)
        ]
        for index, batch in enumerate(batches):
            batch_failed = _process_batch(
                store=store,
                fetcher=fetcher,
                batch=batch,
                include_corporate_actions=include_corporate_actions,
                stats=stats,
            )
            failed_tickers.extend(batch_failed)
            logger.info(
                "Batch %d/%d: size=%d failed=%d news_inserted=%d",
                index + 1,
                len(batches),
                len(batch),
                len(batch_failed),
                stats["news_inserted"],
            )
            if index + 1 < len(batches) and batch_pause_seconds > 0:
                logger.info("Pausing %.0fs before next batch", batch_pause_seconds)
                time.sleep(batch_pause_seconds)

        if failed_tickers:
            logger.info(
                "Retrying %d failed tickers with slower settings",
                len(failed_tickers),
            )
            retry_fetcher = NewsFetcher(
                max_workers=max(2, max_workers // 2),
                request_delay=max(request_delay * 2, 0.5),
                retry_count=5,
            )
            # Adjust stats: subtract prior failures; retry pass will recount.
            stats["tickers_failed"] -= len(failed_tickers)
            stats["tickers_ok"] -= 0
            retry_failed = _process_batch(
                store=store,
                fetcher=retry_fetcher,
                batch=failed_tickers,
                include_corporate_actions=include_corporate_actions,
                stats=stats,
            )
            if retry_failed:
                logger.warning(
                    "%d tickers still failed after retry pass",
                    len(retry_failed),
                )

        stats["retention_deleted"] = store.apply_retention()
        stats["duration_seconds"] = round(time.perf_counter() - started, 1)
        store.finish_fetch_run(run_id, status="completed", stats=stats)
        return stats

    except Exception as exc:
        stats["duration_seconds"] = round(time.perf_counter() - started, 1)
        store.finish_fetch_run(
            run_id,
            status="failed",
            stats=stats,
            error_message=str(exc),
        )
        raise
