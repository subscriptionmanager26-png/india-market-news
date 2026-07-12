from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewsItem:
    ticker: str
    title: str
    summary: str
    published_at: datetime | None
    company_name: str
    content_hash: str


@dataclass
class CorporateActionItem:
    ticker: str
    event_type: str
    event_date: str
    content_hash: str
    details: str = ""
    date_label: str = ""
    document_url: str = ""


@dataclass
class TickerSnapshot:
    ticker: str
    exchange: str
    company_name: str
    url: str
    tcm_id: int | None
    news: list[NewsItem] = field(default_factory=list)
    corporate_actions: list[CorporateActionItem] = field(default_factory=list)
    error: str | None = None
