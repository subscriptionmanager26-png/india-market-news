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
        # Prefer richer news summaries when present.
        if len(row.get("summary", "") or "") > len(existing.get("summary", "") or ""):
            seen[row_key] = row
            continue
        # Prefer richer corporate-action payloads.
        row_score = len(row.get("details", "") or "") + len(row.get("document_url", "") or "")
        existing_score = len(existing.get("details", "") or "") + len(
            existing.get("document_url", "") or ""
        )
        if row_score > existing_score:
            seen[row_key] = row
    return list(seen.values())


def _existing_hash_set(client: Client, table: str, hashes: list[str]) -> set[str]:
    found: set[str] = set()
    chunk_size = 100
    for offset in range(0, len(hashes), chunk_size):
        chunk = hashes[offset : offset + chunk_size]
        result = (
            client.table(table)
            .select("content_hash")
            .in_("content_hash", chunk)
            .execute()
        )
        for row in result.data or []:
            found.add(row["content_hash"])
    return found


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
        url = os.environ.get("SUPABASE_URL", "https://imrcllmpldvjoyjyluhr.supabase.co")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("service_role")
        if not key:
            raise KeyError("SUPABASE_SERVICE_ROLE_KEY or service_role must be set")
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
        # Only persist columns that exist on mn_fetch_runs (ignore extras like
        # raw_files_saved which are useful in logs/artifacts only).
        known = {
            "tickers_total",
            "tickers_ok",
            "tickers_failed",
            "news_seen",
            "news_inserted",
            "news_skipped",
            "corp_seen",
            "corp_inserted",
            "corp_skipped",
            "retention_deleted",
            "duration_seconds",
        }
        payload = {
            "status": status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error_message": error_message,
            **{key: value for key, value in stats.items() if key in known},
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
                    "details": item.details or "",
                    "date_label": item.date_label or "",
                    "document_url": item.document_url or "",
                    "last_seen_at": datetime.now(timezone.utc).isoformat(),
                }
                for item in items
            ],
            key="content_hash",
        )

        hashes = [row["content_hash"] for row in rows]
        existing = _existing_hash_set(self.client, TABLE_CORP, hashes)
        inserted = sum(1 for row in rows if row["content_hash"] not in existing)
        updated = len(rows) - inserted

        # Always use SQL upsert so enrichment fields overwrite empty older rows.
        for offset in range(0, len(rows), UPSERT_CHUNK_SIZE):
            chunk = rows[offset : offset + UPSERT_CHUNK_SIZE]
            self.client.rpc("mn_upsert_corporate_actions", {"rows": chunk}).execute()

        return inserted, updated

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
        return len(_existing_hash_set(self.client, table, hashes))


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
