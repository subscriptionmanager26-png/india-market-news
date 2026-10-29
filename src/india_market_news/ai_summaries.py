"""Cloud-agent summarisation: pending queue + upsert to mn_news_ai_summaries."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from india_market_news.supabase_store import SupabaseStore

logger = logging.getLogger(__name__)

TABLE_AI = "mn_news_ai_summaries"
TABLE_NEWS = "mn_news_items"
DEFAULT_MODEL = "cursor:composer-2.5"
DEFAULT_LOOKBACK_DAYS = 14
PAGE_SIZE = 1000


@dataclass
class PendingStory:
    """One TradingView/Reuters story (possibly many tickers)."""

    source_external_id: str
    title: str
    body_markdown: str
    source: str
    published_at: str | None
    tickers: list[str]
    missing_tickers: list[str]

    def to_agent_dict(self) -> dict[str, Any]:
        return asdict(self)


def combined_external_id(source_external_id: str, title: str) -> tuple[str, str]:
    """Match legacy Edge function: combined:<sha256> and shared content_hash."""
    digest = hashlib.sha256(f"{source_external_id}\n{title}".encode()).hexdigest()
    return f"combined:{digest}", digest


def _load_covered_pairs(client: Any, lookback_days: int) -> set[tuple[str, str]]:
    """(ticker, source_external_id) pairs that already have an AI summary row."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    covered: set[tuple[str, str]] = set()
    offset = 0
    while True:
        result = (
            client.table(TABLE_AI)
            .select("ticker,source_external_ids")
            .gte("generated_at", cutoff)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = result.data or []
        for row in batch:
            ticker = str(row.get("ticker") or "").upper()
            for ext in row.get("source_external_ids") or []:
                if ext:
                    covered.add((ticker, str(ext)))
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return covered


def list_pending_stories(
    store: SupabaseStore,
    *,
    limit: int = 40,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_news_scan: int = 4000,
) -> list[PendingStory]:
    """Stories with at least one ticker not yet present in mn_news_ai_summaries."""
    client = store.client
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    covered = _load_covered_pairs(client, lookback_days + 7)

    groups: dict[str, dict[str, Any]] = {}
    offset = 0
    scanned = 0
    while scanned < max_news_scan:
        result = (
            client.table(TABLE_NEWS)
            .select("ticker,title,summary,external_id,source,published_at,first_seen_at")
            .gte("first_seen_at", cutoff)
            .order("first_seen_at", desc=True)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = result.data or []
        if not batch:
            break
        for row in batch:
            ext = row.get("external_id")
            if not ext:
                continue
            ext = str(ext)
            ticker = str(row.get("ticker") or "").upper()
            if not ticker:
                continue
            if (ticker, ext) in covered:
                continue
            entry = groups.get(ext)
            if entry is None:
                groups[ext] = {
                    "source_external_id": ext,
                    "title": row.get("title") or "",
                    "body_markdown": row.get("summary") or "",
                    "source": row.get("source") or "",
                    "published_at": row.get("published_at"),
                    "tickers": {ticker},
                }
            else:
                entry["tickers"].add(ticker)
                if len(str(row.get("summary") or "")) > len(str(entry["body_markdown"])):
                    entry["body_markdown"] = row.get("summary") or ""
        scanned += len(batch)
        offset += PAGE_SIZE
        if len(batch) < PAGE_SIZE:
            break

    pending: list[PendingStory] = []
    for entry in groups.values():
        tickers = sorted(entry["tickers"])
        missing = [t for t in tickers if (t, entry["source_external_id"]) not in covered]
        if not missing:
            continue
        pending.append(
            PendingStory(
                source_external_id=entry["source_external_id"],
                title=entry["title"],
                body_markdown=entry["body_markdown"],
                source=entry["source"],
                published_at=entry["published_at"],
                tickers=tickers,
                missing_tickers=missing,
            )
        )

    pending.sort(key=lambda s: s.published_at or "", reverse=True)
    return pending[:limit]


def build_summary_rows(
    *,
    story: dict[str, Any],
    model: str = DEFAULT_MODEL,
    emit_whatsapp: bool = True,
) -> list[dict[str, Any]]:
    """Build mn_news_ai_summaries rows from agent output for one story."""
    source_ext = str(story["source_external_id"])
    title = str(story.get("title") or "")
    ai_bullets = str(story.get("ai_bullets") or "").strip()
    if not ai_bullets:
        raise ValueError("ai_bullets is required")

    tickers = story.get("tickers") or story.get("missing_tickers") or []
    tickers = [str(t).upper() for t in tickers if t]
    if not tickers:
        raise ValueError("tickers required")

    combined_ext, content_hash = combined_external_id(source_ext, title)
    as_of = story.get("as_of_date") or date.today().isoformat()
    now = datetime.now(timezone.utc).isoformat()
    story_count = int(story.get("story_count") or 1)
    new_info = bool(story.get("new_info", True))

    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        rows.append(
            {
                "external_id": combined_ext,
                "ticker": ticker,
                "content_hash": content_hash,
                "type": "Stock",
                "subject": ticker,
                "as_of_date": as_of,
                "ai_bullets": ai_bullets,
                "title": title,
                "model": model,
                "generated_at": now,
                "new_info": new_info,
                "story_count": story_count,
                "source_external_ids": [source_ext],
                "emit_whatsapp": emit_whatsapp,
            }
        )
    return rows


def _upsert_summary_row(client: Any, row: dict[str, Any]) -> None:
    """One row per (ticker, combined external_id)."""
    existing = (
        client.table(TABLE_AI)
        .select("id")
        .eq("ticker", row["ticker"])
        .eq("external_id", row["external_id"])
        .limit(1)
        .execute()
    )
    data = existing.data or []
    if data:
        client.table(TABLE_AI).update(row).eq("id", data[0]["id"]).execute()
    else:
        client.table(TABLE_AI).insert(row).execute()


def apply_agent_summaries(
    store: SupabaseStore,
    stories: list[dict[str, Any]],
    *,
    model: str | None = None,
    emit_whatsapp: bool = True,
) -> dict[str, int]:
    """Write agent-produced summaries to Supabase."""
    model = model or DEFAULT_MODEL
    rows: list[dict[str, Any]] = []
    for story in stories:
        rows.extend(
            build_summary_rows(
                story=story,
                model=model,
                emit_whatsapp=emit_whatsapp,
            )
        )

    client = store.client
    for row in rows:
        _upsert_summary_row(client, row)

    return {"rows": len(rows), "stories": len(stories)}
