"""Detect URLs in archived messages, fetch them, and store a Claude-generated
summary in link_summaries. The links_fts contentless FTS5 index makes the
summaries searchable alongside messages and attachment descriptions."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
import trafilatura
from anthropic import AsyncAnthropic

from ceo_bot.config import settings
from ceo_bot.db import cursor

log = structlog.get_logger()

_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

URL_RE = re.compile(r"https?://[^\s<>\"'`)]+", re.IGNORECASE)
FETCH_TIMEOUT_S = 10.0
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 15_000  # cap what we send to the LLM

SUMMARY_PROMPT = """Summarize the page content below in 2-4 sentences. Be factual,
specific, and concise — capture what the page is about, the main claim or topic,
and any concrete details (dates, names, numbers) that would help someone recall
this page later. Do not editorialize.

CONTENT:
"""


def extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in URL_RE.findall(text or ""):
        url = raw.rstrip(".,;:!?]})>'\"")  # strip common trailing punctuation
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _domain_tokens(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return ""
    parts = [p for p in host.split(".") if p and p not in ("www",)]
    return " ".join(parts)


async def _fetch(url: str) -> tuple[str, str] | None:
    """Return (title, main_text) or None on failure."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT_S) as client:
            r = await client.get(url, headers={"User-Agent": "ceo-bot/1.0 (+link summarizer)"})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("link.fetch_failed", url=url, error=str(exc))
        return None
    ct = (r.headers.get("content-type") or "").lower()
    if "html" not in ct and "text" not in ct:
        log.info("link.skipped_non_html", url=url, content_type=ct)
        return None
    if len(r.content) > MAX_BODY_BYTES:
        log.info("link.skipped_too_large", url=url, bytes=len(r.content))
        return None
    extracted = trafilatura.extract(
        r.text, include_comments=False, include_tables=False, with_metadata=True
    )
    if not extracted:
        return None
    title = ""
    body = extracted
    # trafilatura with with_metadata=True prefixes a header; grab the title line if present.
    first_line, _, rest = extracted.partition("\n")
    if first_line and len(first_line) < 200:
        title, body = first_line.strip(), rest.lstrip()
    return title[:300], body[:MAX_TEXT_CHARS]


async def _summarize(title: str, body: str) -> str:
    resp = await _client.messages.create(
        model=settings.anthropic_model,
        max_tokens=400,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": SUMMARY_PROMPT + (title + "\n\n" if title else "") + body},
                ],
            }
        ],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def _refresh_fts(link_id: int, body: str) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM links_fts WHERE rowid=?", (link_id,))
        if body:
            cur.execute("INSERT INTO links_fts(rowid, body) VALUES (?, ?)", (link_id, body))


def _claim_or_get(url: str, message_id: int) -> tuple[int, bool]:
    """Insert a pending row for this URL, or return the existing one. Returns (id, is_new)."""
    now = datetime.now(UTC).isoformat()
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO link_summaries (url, status, first_message_id, created_at)
            VALUES (?, 'pending', ?, ?)
            ON CONFLICT(url) DO NOTHING
            """,
            (url, message_id, now),
        )
        inserted = cur.rowcount > 0
        row = cur.execute("SELECT id FROM link_summaries WHERE url=?", (url,)).fetchone()
    return row["id"], inserted


def _write_result(
    link_id: int, status: str, title: str = "", summary: str = "", error: str = ""
) -> None:
    now = datetime.now(UTC).isoformat()
    with cursor() as cur:
        cur.execute(
            """
            UPDATE link_summaries
               SET status=?, title=?, summary=?, error=?, fetched_at=?
             WHERE id=?
            """,
            (status, title or None, summary or None, error or None, now, link_id),
        )


async def enrich_link(url: str, message_id: int) -> None:
    try:
        link_id, is_new = await asyncio.to_thread(_claim_or_get, url, message_id)
        if not is_new:
            return  # already processed (or being processed) — dedup
        fetched = await _fetch(url)
        if fetched is None:
            await asyncio.to_thread(_write_result, link_id, "failed", error="fetch_or_parse_failed")
            return
        title, body = fetched
        summary = await _summarize(title, body)
        await asyncio.to_thread(_write_result, link_id, "success", title=title, summary=summary)
        fts_body = " ".join(filter(None, ["link url", _domain_tokens(url), title, summary]))
        await asyncio.to_thread(_refresh_fts, link_id, fts_body)
        log.info("link.summarized", url=url, link_id=link_id, summary_chars=len(summary))
    except Exception as exc:
        log.exception("link.enrichment_failed", url=url)
        try:
            await asyncio.to_thread(_write_result, link_id, "failed", error=str(exc)[:500])
        except Exception:
            pass


def schedule_links_for_message(message_id: int, content: str) -> None:
    """Spawn enrichment tasks for every URL in the message body."""
    for url in extract_urls(content):
        asyncio.create_task(enrich_link(url, message_id))
