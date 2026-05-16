from __future__ import annotations

from typing import Any

from ceo_bot.db import cursor

TOOL_SCHEMA: dict[str, Any] = {
    "name": "search_history",
    "description": (
        "Full-text search the archive of past messages between the users. "
        "Returns up to 20 matching messages ordered by recency."
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
        rows = cur.execute(
            """
            SELECT m.id, m.author_name, m.content, m.created_at
            FROM messages_fts f
            JOIN messages m ON m.id = f.rowid
            WHERE messages_fts MATCH ?
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    return {
        "matches": [
            {
                "id": r["id"],
                "author": r["author_name"],
                "content": r["content"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }
