from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from india_market_news.models import CorporateActionItem, NewsItem, TickerSnapshot

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90
UPSERT_CHUNK_SIZE = 200


def _dedupe_news_items(items: list[NewsItem]) -> list[NewsItem]:
    """Keep one row per content_hash; prefer the longest summary."""
    by_hash: dict[str, NewsItem] = {}
    for item in items:
        existing = by_hash.get(item.content_hash)
        if existing is None or len(item.summary) > len(existing.summary):
            by_hash[item.content_hash] = item
    return list(by_hash.values())


def _dedupe_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Keep one row per unique key within a single upsert batch."""
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_key = row[key]
        existing = seen.get(row_key)
        if existing is None:
            seen[row_key] = row
            continue
        if key == "content_hash" and len(row.get("summary", "")) > len(
            existing.get("summary", "")
        ):
            seen[row_key] = row
    return list(seen.values())

# Tables live in public schema (mn_ prefix) for reliable PostgREST access.
TABLE_TICKERS = "mn_tickers"
TABLE_FETCH_RUNS = "mn_fetch_runs"
TABLE_NEWS = "mn_news_items"
TABLE_CORP = "mn_corporate_actions"
RPC_PURGE = "mn_purge_old_news"


class SupabaseStore:
    def __init__(self, client: Client):
        self.client = client

    @classmethod
    def from_env(cls) -> SupabaseStore:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        return cls(create_client(url, key))

    def start_fetch_run(self, run_type: str) -> str:
        row = (
            self.client.table(TABLE_FETCH_RUNS)
            .insert({"run_type": run_type, "status": "running"})
            .execute()
        )
        return row.data[0]["id"]

    def finish_fetch_run(
        self,
        run_id: str,
        *,
        status: str,
        stats: dict[str, Any],
        error_message: str | None = None,
    ) -> None:
        payload = {
            "status": status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error_message": error_message,
            **stats,
        }
        self.client.table(TABLE_FETCH_RUNS).update(payload).eq("id", run_id).execute()

    def upsert_news(self, items: list[NewsItem]) -> tuple[int, int]:
        if not items:
            return 0, 0

        items = _dedupe_news_items(items)
        rows = _dedupe_rows(
            [
            {
                "content_hash": item.content_hash,
                "ticker": item.ticker,
                "company_name": item.company_name,
                "title": item.title,
                "summary": item.summary,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "source": "zerodha",
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
            }
            for item in items
            ],
            key="content_hash",
        )

        before = self._count_hashes(TABLE_NEWS, [row["content_hash"] for row in rows])
        for offset in range(0, len(rows), UPSERT_CHUNK_SIZE):
            chunk = rows[offset : offset + UPSERT_CHUNK_SIZE]
            self.client.table(TABLE_NEWS).upsert(
                chunk,
                on_conflict="content_hash",
                ignore_duplicates=False,
            ).execute()
        inserted = max(len(rows) - before, 0)
        updated = len(rows) - inserted
        return inserted, updated

    def upsert_corporate_actions(
        self, items: list[CorporateActionItem]
    ) -> tuple[int, int]:
        if not items:
            return 0, 0

        rows = _dedupe_rows(
            [
                {
                    "content_hash": item.content_hash,
                    "ticker": item.ticker,
                    "event_type": item.event_type,
                    "event_date_raw": item.event_date,
                    "details": item.details,
                    "date_label": item.date_label,
                    "document_url": item.document_url,
                    "last_seen_at": datetime.now(timezone.utc).isoformat(),
                }
                for item in items
            ],
            key="content_hash",
        )

        before = self._count_hashes(TABLE_CORP, [row["content_hash"] for row in rows])
        for offset in range(0, len(rows), UPSERT_CHUNK_SIZE):
            chunk = rows[offset : offset + UPSERT_CHUNK_SIZE]
            self.client.table(TABLE_CORP).upsert(
                chunk,
                on_conflict="content_hash",
                ignore_duplicates=False,
            ).execute()
        inserted = max(len(rows) - before, 0)
        return inserted, len(rows) - inserted

    def apply_retention(self, days: int = RETENTION_DAYS) -> int:
        result = self.client.rpc(RPC_PURGE, {"retention_days": days}).execute()
        return int(result.data or 0)

    def sync_tickers(self, tickers: list[dict[str, str]]) -> None:
        if not tickers:
            return
        rows = [
            {
                "symbol": row["symbol"],
                "nse_symbol": row["nse_symbol"],
                "company_name": row.get("company_name") or "",
                "series": row.get("series") or "",
                "isin": row.get("isin") or "",
            }
            for row in tickers
        ]
        self.client.table(TABLE_TICKERS).upsert(rows, on_conflict="symbol").execute()

    def _count_hashes(self, table: str, hashes: list[str]) -> int:
        if not hashes:
            return 0
        total = 0
        chunk_size = 100
        for offset in range(0, len(hashes), chunk_size):
            chunk = hashes[offset : offset + chunk_size]
            result = (
                self.client.table(table)
                .select("content_hash")
                .in_("content_hash", chunk)
                .execute()
            )
            total += len(result.data or [])
        return total


def snapshots_to_items(
    snapshots: list[TickerSnapshot],
) -> tuple[list[NewsItem], list[CorporateActionItem]]:
    news: list[NewsItem] = []
    corp: list[CorporateActionItem] = []
    for snapshot in snapshots:
        if snapshot.error:
            continue
        news.extend(snapshot.news)
        corp.extend(snapshot.corporate_actions)
    return news, corp
