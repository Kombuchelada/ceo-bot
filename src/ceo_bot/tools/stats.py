"""On-demand chat statistics tool. Computes volume / content / pattern stats
from the archive over a chosen window, returns a JSON-friendly dict for Claude
to summarize."""

from __future__ import annotations

import re
import statistics
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import emoji as emoji_lib

from ceo_bot.db import cursor

TOOL_SCHEMA: dict[str, Any] = {
    "name": "get_chat_stats",
    "description": (
        "Aggregate statistics over messages, attachments, and shared links "
        "between the users. Use this when asked about totals, who-talks-more, "
        "top words/emoji, response times, or activity patterns. Returns a "
        "structured dict; format it conversationally."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["all", "7d", "30d", "ytd"],
                "description": "Time window: 'all', last 7 days, last 30 days, or year to date.",
            }
        },
    },
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "at",
    "for", "with", "by", "from", "as", "is", "was", "are", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "i", "me", "my", "you",
    "your", "we", "us", "our", "he", "she", "they", "them", "it", "its", "this",
    "that", "these", "those", "so", "not", "no", "yes", "yeah", "ok", "okay",
    "just", "what", "when", "where", "why", "how", "can", "will", "would",
    "could", "should", "im", "ive", "ill", "id", "lol", "haha", "u", "ur",
    "youre", "thats", "dont", "didnt", "wont", "cant", "isnt", "its", "got",
    "get", "go", "going", "gonna", "wanna", "really", "very", "like", "yeah",
    "back", "now", "then", "there", "here", "out", "up", "down", "off", "over",
    "again", "still", "well", "much", "some", "any", "all", "one", "two", "even",
    "also", "today", "tomorrow", "tonight",
}

WORD_RE = re.compile(r"[A-Za-z][A-Za-z']{2,}")
QUIET_GAP = timedelta(hours=4)


def _scope_start(scope: str) -> datetime | None:
    now = datetime.now(UTC)
    if scope == "7d":
        return now - timedelta(days=7)
    if scope == "30d":
        return now - timedelta(days=30)
    if scope == "ytd":
        return datetime(now.year, 1, 1, tzinfo=UTC)
    return None  # 'all'


def _fetch_messages(start: datetime | None) -> list[dict[str, Any]]:
    with cursor() as cur:
        if start:
            rows = cur.execute(
                """
                SELECT id, channel_id, author_id, author_name, content, created_at
                FROM messages WHERE created_at >= ? ORDER BY created_at
                """,
                (start.isoformat(),),
            ).fetchall()
        else:
            rows = cur.execute(
                """
                SELECT id, channel_id, author_id, author_name, content, created_at
                FROM messages ORDER BY created_at
                """
            ).fetchall()
    return [dict(r) for r in rows]


def _fetch_aux(start: datetime | None) -> tuple[list[dict], list[dict]]:
    with cursor() as cur:
        if start:
            atts = cur.execute(
                "SELECT content_type FROM attachments WHERE created_at >= ?",
                (start.isoformat(),),
            ).fetchall()
            links = cur.execute(
                "SELECT url FROM link_summaries WHERE created_at >= ?",
                (start.isoformat(),),
            ).fetchall()
        else:
            atts = cur.execute("SELECT content_type FROM attachments").fetchall()
            links = cur.execute("SELECT url FROM link_summaries").fetchall()
    return [dict(a) for a in atts], [dict(l) for l in links]


def _attachment_kind(ct: str | None) -> str:
    if not ct:
        return "other"
    if ct.startswith("image/"):
        return "images"
    if ct.startswith("video/"):
        return "videos"
    if ct.startswith("audio/"):
        return "audio"
    return "other"


def _top_words(texts: list[str], n: int = 10) -> list[tuple[str, int]]:
    c: Counter[str] = Counter()
    for t in texts:
        for w in WORD_RE.findall(t.lower()):
            if w not in STOPWORDS:
                c[w] += 1
    return c.most_common(n)


def _top_emoji(texts: list[str], n: int = 5) -> list[tuple[str, int]]:
    c: Counter[str] = Counter()
    for t in texts:
        for ch in emoji_lib.distinct_emoji_list(t):
            c[ch] += t.count(ch)
    return c.most_common(n)


def _question_ratio(texts: list[str]) -> float:
    if not texts:
        return 0.0
    q = sum(1 for t in texts if t.rstrip().endswith("?"))
    return round(q / len(texts), 3)


def _day_streak(dates: list[str]) -> int:
    """Longest run of consecutive UTC dates with at least one message."""
    days = sorted({d[:10] for d in dates})
    if not days:
        return 0
    best = cur_run = 1
    prev = datetime.strptime(days[0], "%Y-%m-%d").date()
    for d in days[1:]:
        cur_d = datetime.strptime(d, "%Y-%m-%d").date()
        if (cur_d - prev).days == 1:
            cur_run += 1
            best = max(best, cur_run)
        else:
            cur_run = 1
        prev = cur_d
    return best


def _longest_gap(dates: list[str]) -> dict[str, Any]:
    if len(dates) < 2:
        return {"hours": 0, "start": None, "end": None}
    parsed = sorted(datetime.fromisoformat(d) for d in dates)
    best = timedelta(0)
    pair = (parsed[0], parsed[1])
    for a, b in zip(parsed, parsed[1:], strict=True):
        gap = b - a
        if gap > best:
            best = gap
            pair = (a, b)
    return {
        "hours": round(best.total_seconds() / 3600, 1),
        "start": pair[0].isoformat(),
        "end": pair[1].isoformat(),
    }


def _response_times_and_initiations(msgs: list[dict]) -> dict[str, Any]:
    """Per-author median reply time (s) and # of initiations after a quiet gap.
    Reply: A's message followed by B's message in the same channel within 24h.
    Initiation: a message whose previous message in the same channel was >4h ago."""
    by_channel: dict[int, list[dict]] = {}
    for m in msgs:
        by_channel.setdefault(m["channel_id"], []).append(m)
    reply_times: dict[str, list[float]] = {}
    initiations: Counter[str] = Counter()
    for chan_msgs in by_channel.values():
        chan_msgs.sort(key=lambda m: m["created_at"])
        for i, m in enumerate(chan_msgs):
            ts = datetime.fromisoformat(m["created_at"])
            if i == 0:
                initiations[m["author_name"]] += 1
                continue
            prev = chan_msgs[i - 1]
            prev_ts = datetime.fromisoformat(prev["created_at"])
            gap = ts - prev_ts
            if gap >= QUIET_GAP:
                initiations[m["author_name"]] += 1
            if (
                prev["author_id"] != m["author_id"]
                and gap < timedelta(hours=24)
            ):
                reply_times.setdefault(m["author_name"], []).append(gap.total_seconds())
    median_secs = {
        author: round(statistics.median(secs), 1) for author, secs in reply_times.items()
    }
    return {
        "median_reply_seconds_by_author": median_secs,
        "initiations_after_quiet_gap_by_author": dict(initiations),
    }


def _hour_of_day_histogram(dates: list[str]) -> dict[int, int]:
    c: Counter[int] = Counter()
    for d in dates:
        c[datetime.fromisoformat(d).hour] += 1
    return dict(sorted(c.items()))


def _dow_histogram(dates: list[str]) -> dict[str, int]:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    c: Counter[str] = Counter()
    for d in dates:
        c[names[datetime.fromisoformat(d).weekday()]] += 1
    return {n: c.get(n, 0) for n in names}


def compute_stats(scope: str = "all") -> dict[str, Any]:
    """Pure function — the weekly summary calls this too."""
    start = _scope_start(scope)
    msgs = _fetch_messages(start)
    atts, links = _fetch_aux(start)
    if not msgs:
        return {"scope": scope, "messages_total": 0, "note": "no activity in this window"}

    # Per-author buckets
    by_author: dict[str, list[dict]] = {}
    for m in msgs:
        by_author.setdefault(m["author_name"], []).append(m)
    per_author: dict[str, Any] = {}
    for author, items in by_author.items():
        texts = [m["content"] for m in items if m["content"]]
        per_author[author] = {
            "messages": len(items),
            "avg_message_length_chars": round(
                sum(len(t) for t in texts) / len(texts), 1
            ) if texts else 0,
            "question_ratio": _question_ratio(texts),
            "top_words": _top_words(texts),
            "top_emoji": _top_emoji(texts),
        }

    dates = [m["created_at"] for m in msgs]
    days_span = (
        datetime.fromisoformat(dates[-1]).date()
        - datetime.fromisoformat(dates[0]).date()
    ).days + 1
    by_date: Counter[str] = Counter(d[:10] for d in dates)
    peak_day, peak_count = by_date.most_common(1)[0]

    att_kinds: Counter[str] = Counter(_attachment_kind(a["content_type"]) for a in atts)
    link_domains: Counter[str] = Counter()
    for l in links:
        host = urlparse(l["url"]).hostname or ""
        host = host.removeprefix("www.")
        if host:
            link_domains[host] += 1

    patterns = _response_times_and_initiations(msgs)

    return {
        "scope": scope,
        "window_start": dates[0],
        "window_end": dates[-1],
        "days_span": days_span,
        "messages_total": len(msgs),
        "messages_per_day_avg": round(len(msgs) / days_span, 2),
        "peak_day": {"date": peak_day, "messages": peak_count},
        "longest_streak_days": _day_streak(dates),
        "longest_gap": _longest_gap(dates),
        "hour_of_day": _hour_of_day_histogram(dates),
        "day_of_week": _dow_histogram(dates),
        "per_author": per_author,
        "attachments": dict(att_kinds),
        "attachments_total": len(atts),
        "links_total": len(links),
        "top_link_domains": link_domains.most_common(5),
        **patterns,
    }


async def run(*, user_id: int, scope: str = "all", **_: Any) -> dict[str, Any]:
    if scope not in ("all", "7d", "30d", "ytd"):
        scope = "all"
    return compute_stats(scope)
