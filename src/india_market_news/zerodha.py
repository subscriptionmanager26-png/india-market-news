from __future__ import annotations

import html as htmlmod
import logging
import re
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from india_market_news.dedup import corporate_action_hash, news_content_hash
from india_market_news.html_markdown import html_to_markdown
from india_market_news.models import CorporateActionItem, NewsItem, TickerSnapshot

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36"
)
BASE_URL = "https://zerodha.com/markets/stocks"


def _strip_html(fragment: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", fragment, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return htmlmod.unescape(re.sub(r"\s+", " ", text)).strip()


def _section(html: str, section_id: str) -> str:
    match = re.search(
        rf'<div id="{re.escape(section_id)}"[^>]*>(.*)',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return ""

    chunk = match.group(1)
    end = re.search(r'<div id="[^"]+" class="subtab_content', chunk)
    return chunk[: end.start()] if end else chunk


def _parse_company_name(html: str, ticker: str) -> str:
    title_match = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
    if title_match:
        title = _strip_html(title_match.group(1))
        if title:
            return title.split(" Share Price", 1)[0].strip()
    return ticker


def _parse_tcm_id(html: str) -> int | None:
    match = re.search(
        r'<script id="tcmId" type="application/json">(\d+)</script>',
        html,
    )
    return int(match.group(1)) if match else None


def _parse_published_at(raw_date: str) -> datetime | None:
    raw_date = raw_date.strip()
    if not raw_date:
        return None
    try:
        return date_parser.parse(raw_date, dayfirst=False)
    except (ValueError, TypeError, OverflowError):
        return None


def parse_zerodha_page(html: str, *, exchange: str, ticker: str, url: str) -> TickerSnapshot:
    company_name = _parse_company_name(html, ticker)
    tcm_id = _parse_tcm_id(html)
    news = _parse_news(html, ticker=ticker, company_name=company_name)
    corporate_actions = _parse_corporate_actions(html, ticker=ticker)

    error = None
    if not news and "Page not found" in html:
        error = "Ticker not found on Zerodha Markets"

    return TickerSnapshot(
        ticker=ticker,
        exchange=exchange,
        company_name=company_name,
        url=url,
        tcm_id=tcm_id,
        news=news,
        corporate_actions=corporate_actions,
        error=error,
    )


def _extract_news_summary(block: str) -> str:
    """Pull Reuters summary as Markdown from Zerodha's news_detail block."""
    detail = re.search(
        r'<div class="news_detail"[^>]*>(.*)',
        block,
        flags=re.DOTALL | re.IGNORECASE,
    )
    chunk = detail.group(1) if detail else block
    best = ""
    for pattern in (
        r'<div class="news_story">(.*?)</div>\s*</div>',
        r'<div class="full_story">(.*?)</div>',
        r'<div class="news_story">(.*?)</div>',
    ):
        match = re.search(pattern, chunk, flags=re.DOTALL | re.IGNORECASE)
        if not match:
            continue
        text = html_to_markdown(match.group(1))
        if len(text) > len(best):
            best = text
    return best


def _parse_news(html: str, *, ticker: str, company_name: str) -> list[NewsItem]:
    section = _section(html, "news")
    if not section:
        return []

    items: list[NewsItem] = []
    for block in re.findall(
        r'<div class="news_wrapper">(.*?)(?=<div class="news_wrapper">|$)',
        section,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        headline_match = re.search(
            r'<div class="news_headline">(.*?)</div>',
            block,
            flags=re.DOTALL,
        )
        if not headline_match:
            continue

        title = _strip_html(headline_match.group(1))
        if not title:
            continue

        date_match = re.search(
            r'<div class="timestamp">\s*<span>(.*?)</span>',
            block,
            flags=re.DOTALL,
        )
        published_raw = _strip_html(date_match.group(1)) if date_match else ""
        published_at = _parse_published_at(published_raw) if published_raw else None
        summary = _extract_news_summary(block)

        items.append(
            NewsItem(
                ticker=ticker,
                title=title,
                summary=summary,
                published_at=published_at,
                company_name=company_name,
                content_hash=news_content_hash(
                    ticker,
                    title,
                    published_at.isoformat() if published_at else published_raw,
                ),
            )
        )

    return items


_DATE_LABELS = {
    "ex date",
    "announcement date",
    "record date",
    "board meeting date",
    "meeting date",
}


def _parse_content_link(part: str) -> tuple[str, str]:
    """Return (url, title) for Annual Report-style content links."""
    match = re.search(
        r"<a[^>]*class=['\"]?content-link['\"]?[^>]*href=(['\"]?)([^'\"\s>]+)\1[^>]*>(.*?)</a>",
        part,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"<a[^>]*class=['\"]?content-link['\"]?[^>]*href=([^\s>]+)[^>]*>(.*?)</a>",
            part,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not match:
            return "", ""
        return match.group(1).strip("\"'"), _strip_html(match.group(2))
    return match.group(2).strip(), _strip_html(match.group(3))


def _parse_corporate_actions(html: str, *, ticker: str) -> list[CorporateActionItem]:
    section = _section(html, "corporate_ations")
    if not section:
        return []

    actions: list[CorporateActionItem] = []
    seen: set[str] = set()
    for part in re.split(r'<div class="event"[^>]*>', section)[1:]:
        name_match = re.search(
            r'<div class="event_name"[^>]*>(.*?)</div>',
            part,
            flags=re.DOTALL | re.IGNORECASE,
        )
        event_type = _strip_html(name_match.group(1)) if name_match else ""

        document_url, link_title = _parse_content_link(part)
        if not event_type and link_title:
            event_type = link_title
        if not event_type:
            continue

        timestamp_match = re.search(
            r'<div class="timestamp"[^>]*>(.*?)</div>',
            part,
            flags=re.DOTALL | re.IGNORECASE,
        )
        event_date = _strip_html(timestamp_match.group(1)) if timestamp_match else ""
        if not event_date:
            continue

        date_label = ""
        right_col = re.search(
            r'<div class="right_col"[^>]*>(.*)',
            part,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if right_col:
            label_match = re.search(
                r'<div class="sub-title"[^>]*>(.*?)</div>',
                right_col.group(1),
                flags=re.DOTALL | re.IGNORECASE,
            )
            if label_match:
                date_label = _strip_html(label_match.group(1))

        details = ""
        for subtitle in re.findall(
            r'<div class="sub-title"[^>]*>(.*?)</div>',
            part,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            text = _strip_html(subtitle)
            if not text:
                continue
            if text.lower() in _DATE_LABELS:
                continue
            if link_title and text.lower() == link_title.lower():
                continue
            details = text
            break

        content_hash = corporate_action_hash(ticker, event_type, event_date)
        if content_hash in seen:
            continue
        seen.add(content_hash)

        actions.append(
            CorporateActionItem(
                ticker=ticker,
                event_type=event_type,
                event_date=event_date,
                content_hash=content_hash,
                details=details,
                date_label=date_label,
                document_url=document_url,
            )
        )

    return actions


def fetch_ticker(
    ticker: str,
    *,
    exchange: str = "NSE",
    client: httpx.Client | None = None,
) -> TickerSnapshot:
    url = f"{BASE_URL}/{exchange}/{ticker}/"
    owns_client = client is None
    client = client or httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=25.0,
        follow_redirects=True,
    )

    try:
        response = client.get(url)
        if response.status_code == 404:
            return TickerSnapshot(
                ticker=ticker,
                exchange=exchange,
                company_name=ticker,
                url=url,
                tcm_id=None,
                error="Ticker not found on Zerodha Markets",
            )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "").strip()
            detail = "429 Too Many Requests"
            if retry_after:
                detail = f"{detail}; retry_after={retry_after}"
            return TickerSnapshot(
                ticker=ticker,
                exchange=exchange,
                company_name=ticker,
                url=url,
                tcm_id=None,
                error=detail,
            )
        response.raise_for_status()
        return parse_zerodha_page(response.text, exchange=exchange, ticker=ticker, url=url)
    except httpx.HTTPError as exc:
        logger.warning("Zerodha fetch failed for %s: %s", ticker, exc)
        return TickerSnapshot(
            ticker=ticker,
            exchange=exchange,
            company_name=ticker,
            url=url,
            tcm_id=None,
            error=str(exc),
        )
    finally:
        if owns_client:
            client.close()
