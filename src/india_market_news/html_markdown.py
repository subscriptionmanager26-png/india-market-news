from __future__ import annotations

import html as htmlmod
import re

import html2text

_CONVERTER = html2text.HTML2Text()
_CONVERTER.body_width = 0
_CONVERTER.ignore_images = True
_CONVERTER.protect_links = True
_CONVERTER.unicode_snob = True

_URL_LIKE = re.compile(r"^(?:https?://|mailto:|/|#)", re.IGNORECASE)
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}")


def _strip_disclaimer(html: str) -> str:
    html = re.sub(
        r'<div id="public_disclaimer"[^>]*>.*',
        "",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    return html


def _looks_like_url(href: str) -> bool:
    href = href.strip()
    return bool(href and _URL_LIKE.match(href))


def _normalize_html(fragment: str) -> str:
    """Fix common Reuters/Zerodha HTML quirks before markdown conversion."""

    def fix_table_cell(match: re.Match[str]) -> str:
        tag = match.group(1)
        attrs = match.group(2)
        inner = match.group(3)
        inner = re.sub(r"<br\s*/?>", " / ", inner, flags=re.IGNORECASE)
        inner = re.sub(r"\s+", " ", inner)
        return f"<{tag}{attrs}>{inner}</{tag}>"

    html = re.sub(
        r"<(td|th)([^>]*)>(.*?)</\1>",
        fix_table_cell,
        fragment,
        flags=re.DOTALL | re.IGNORECASE,
    )

    def fix_anchor(match: re.Match[str]) -> str:
        href = htmlmod.unescape(match.group(1).strip())
        text = _strip_inline_html(match.group(2))
        if _looks_like_url(href):
            return match.group(0)
        return text

    html = re.sub(
        r'<a\b[^>]*\bhref=["\']([^"\']*)["\'][^>]*>(.*?)</a>',
        fix_anchor,
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Internal Reuters cross-ref spans → plain text.
    html = re.sub(
        r"<span[^>]*class=\"[^\"]*tr-link[^\"]*\"[^>]*>(.*?)</span>",
        lambda m: _strip_inline_html(m.group(1)),
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    html = re.sub(r"Powered by Tijori\s*", "", html, flags=re.IGNORECASE)
    return html


def _strip_inline_html(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return htmlmod.unescape(re.sub(r"\s+", " ", text)).strip()


def _fix_broken_table_rows(text: str) -> str:
    """Merge table rows split by newlines inside a cell."""
    lines = text.split("\n")
    out: list[str] = []
    in_table = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            out.append(line)
            in_table = False
            i += 1
            continue

        if _TABLE_SEP.match(stripped):
            in_table = True
            out.append(line)
            i += 1
            continue

        if "|" in stripped:
            in_table = True
            out.append(line)
            i += 1
            continue

        if (
            in_table
            and i + 1 < len(lines)
            and "|" in lines[i + 1]
            and not _TABLE_SEP.match(lines[i + 1].strip())
        ):
            merged = f"{stripped} / {lines[i + 1].lstrip()}"
            out.append(merged)
            i += 2
            continue

        out.append(line)
        i += 1

    return "\n".join(out)


def _cleanup_markdown(text: str) -> str:
    text = htmlmod.unescape(text)
    text = re.sub(r"\\-", "-", text)
    text = re.sub(r"\[\[email[^\]]*\]\]\([^)]+\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[([^\]]+)\]\(<[^>]*>\)", r"\1", text)
    text = re.sub(
        r"\[([^\]]+)\]\((?!https?://|mailto:|/)[^)]+\)",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\(\([^)]*\)\)", "", text)
    text = re.sub(r"\(\(\s*$", "", text)
    text = re.sub(r"\n[ \t]+(\* )", r"\n\1", text)
    text = re.sub(r"Powered by Tijori\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = _fix_broken_table_rows(text)
    return text.strip()


def html_to_markdown(fragment: str) -> str:
    """Convert Zerodha/Reuters HTML story body to readable Markdown."""
    if not fragment or not fragment.strip():
        return ""
    cleaned = _strip_disclaimer(fragment)
    cleaned = _normalize_html(cleaned)
    markdown = _CONVERTER.handle(cleaned)
    return _cleanup_markdown(markdown)
