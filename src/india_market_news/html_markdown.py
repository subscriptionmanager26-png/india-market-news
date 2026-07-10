from __future__ import annotations

import html as htmlmod
import re

import html2text

_CONVERTER = html2text.HTML2Text()
_CONVERTER.body_width = 0
_CONVERTER.ignore_images = True
_CONVERTER.protect_links = True
_CONVERTER.unicode_snob = True


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


def _cleanup_markdown(text: str) -> str:
    text = htmlmod.unescape(text)
    text = re.sub(r"\[\[email[^\]]*\]\]\([^)]+\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(\([^)]*\)\)", "", text)
    text = re.sub(r"\(\(\s*$", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_markdown(fragment: str) -> str:
    """Convert Zerodha/Reuters HTML story body to readable Markdown."""
    if not fragment or not fragment.strip():
        return ""
    cleaned = _strip_disclaimer(fragment)
    markdown = _CONVERTER.handle(cleaned)
    return _cleanup_markdown(markdown)
