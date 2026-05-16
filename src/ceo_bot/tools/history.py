from __future__ import annotations

from typing import Any

from ceo_bot.db import cursor

TOOL_SCHEMA: dict[str, Any] = {
    "name": "search_history",
    "description": (
        "Full-text search the archive of past messages between the users. "
        "Matches against message text AND against extracted text/descriptions "
        "of attached images and videos. Returns up to 20 matches by recency."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "FTS5 query string. Plain words are AND-ed. Use quotes for phrases.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 20, max 50).",
                "default": 20,
            },
        },
        "required": ["query"],
    },
}


async def run(*, user_id: int, query: str, limit: int = 20, **_: Any) -> dict[str, Any]:
    limit = max(1, min(int(limit), 50))
    with cursor() as cur:
        msg_rows = cur.execute(
            """
            SELECT m.id, m.author_name, m.content, m.created_at, NULL AS attachment_snippet
            FROM messages_fts f
            JOIN messages m ON m.id = f.rowid
            WHERE messages_fts MATCH ?
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        att_rows = cur.execute(
            """
            SELECT m.id, m.author_name, m.content, m.created_at,
                   a.filename || ': ' || coalesce(a.summary, substr(a.ocr_text, 1, 200)) AS attachment_snippet
            FROM attachments_fts af
            JOIN attachments a ON a.id = af.rowid
            JOIN messages m ON m.id = a.message_id
            WHERE attachments_fts MATCH ?
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()

    seen: dict[int, dict[str, Any]] = {}
    for r in list(msg_rows) + list(att_rows):
        msg_id = r["id"]
        if msg_id in seen:
            if r["attachment_snippet"]:
                seen[msg_id].setdefault("attachments", []).append(r["attachment_snippet"])
            continue
        entry = {
            "id": msg_id,
            "author": r["author_name"],
            "content": r["content"],
            "created_at": r["created_at"],
        }
        if r["attachment_snippet"]:
            entry["attachments"] = [r["attachment_snippet"]]
        seen[msg_id] = entry

    matches = sorted(seen.values(), key=lambda e: e["created_at"], reverse=True)[:limit]
    return {"matches": matches}
