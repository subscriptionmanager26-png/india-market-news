"""CLI for Cursor Cloud Agent news summarisation workflow."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from india_market_news.ai_summaries import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MODEL,
    apply_agent_summaries,
    list_pending_stories,
)
from india_market_news.supabase_store import SupabaseStore


def _cmd_list_pending(args: argparse.Namespace) -> int:
    store = SupabaseStore.from_env()
    pending = list_pending_stories(
        store,
        limit=args.limit,
        lookback_days=args.lookback_days,
    )
    payload = {
        "count": len(pending),
        "model_hint": args.model or DEFAULT_MODEL,
        "stories": [story.to_agent_dict() for story in pending],
        "instructions": (
            "For each story, write ai_bullets (Markdown bullets, tickers/companies in *bold*, "
            "numbers in *bold*). Return JSON: {\"summaries\": [{source_external_id, title, "
            "tickers, ai_bullets, new_info}]}. Then run: "
            "india-market-news-summarize apply --input summaries.json"
        ),
    }
    print(json.dumps(payload, indent=2))
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    path = Path(args.input)
    data = json.loads(path.read_text())
    stories = data.get("summaries") or data
    if not isinstance(stories, list):
        raise SystemExit("Expected JSON list or {\"summaries\": [...]}")

    store = SupabaseStore.from_env()
    stats = apply_agent_summaries(
        store,
        stories,
        model=args.model or DEFAULT_MODEL,
        emit_whatsapp=not args.no_whatsapp,
    )
    print(json.dumps({"ok": True, **stats}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List pending market-news stories and apply Cursor agent summaries"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list-pending", help="Print pending stories as JSON for the agent")
    list_p.add_argument("--limit", type=int, default=40, help="Max stories (default 40)")
    list_p.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"News lookback window (default {DEFAULT_LOOKBACK_DAYS})",
    )
    list_p.add_argument("--model", default=None, help="Model label stored in mn_news_ai_summaries")
    list_p.set_defaults(func=_cmd_list_pending)

    apply_p = sub.add_parser("apply", help="Upsert agent JSON into mn_news_ai_summaries")
    apply_p.add_argument(
        "--input",
        required=True,
        type=Path,
        help="JSON file with {\"summaries\": [...]}",
    )
    apply_p.add_argument("--model", default=None, help="Model label (default cursor:composer-2.5)")
    apply_p.add_argument(
        "--no-whatsapp",
        action="store_true",
        help="Set emit_whatsapp=false on new rows",
    )
    apply_p.set_defaults(func=_cmd_apply)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
