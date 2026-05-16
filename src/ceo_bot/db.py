from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from ceo_bot.config import settings

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY,            -- Discord message ID (snowflake)
    channel_id    INTEGER NOT NULL,
    guild_id      INTEGER,
    author_id     INTEGER NOT NULL,
    author_name   TEXT NOT NULL,
    content       TEXT NOT NULL,
    reply_to_id   INTEGER,
    created_at    TEXT NOT NULL,                  -- ISO8601 UTC
    edited_at     TEXT,
    deleted_at    TEXT,
    raw_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_channel_time ON messages(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_author_time  ON messages(author_id, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS attachments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id    INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,
    content_type  TEXT,
    size_bytes    INTEGER,
    sha256        TEXT,
    s3_key        TEXT NOT NULL,
    ocr_text      TEXT,
    transcript    TEXT,
    summary       TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attachments_message ON attachments(message_id);

CREATE VIRTUAL TABLE IF NOT EXISTS attachments_fts USING fts5(
    body,
    content='',
    contentless_delete=1
);

CREATE TABLE IF NOT EXISTS reminders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    due_at        TEXT NOT NULL,                  -- ISO8601 UTC
    channel_id    INTEGER NOT NULL,
    user_ids      TEXT NOT NULL,                  -- JSON array of user IDs to ping
    payload       TEXT NOT NULL,                  -- human-readable reminder text
    source_message_id INTEGER REFERENCES messages(id),
    status        TEXT NOT NULL DEFAULT 'pending', -- pending | sent | cancelled
    created_at    TEXT NOT NULL,
    sent_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at);

CREATE TABLE IF NOT EXISTS calendar_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    provider      TEXT NOT NULL DEFAULT 'google',
    external_id   TEXT NOT NULL,
    calendar_id   TEXT NOT NULL,
    summary       TEXT NOT NULL,
    start_at      TEXT NOT NULL,
    end_at        TEXT NOT NULL,
    source_message_id INTEGER REFERENCES messages(id),
    raw_json      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE(provider, external_id)
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    user_id       INTEGER NOT NULL,
    provider      TEXT NOT NULL,
    token_blob    BLOB NOT NULL,                  -- Fernet-encrypted JSON
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (user_id, provider)
);

CREATE TABLE IF NOT EXISTS link_summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT NOT NULL UNIQUE,
    title           TEXT,
    summary         TEXT,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | success | failed | skipped
    error           TEXT,
    first_message_id INTEGER REFERENCES messages(id),
    fetched_at      TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_link_summaries_status ON link_summaries(status);

CREATE VIRTUAL TABLE IF NOT EXISTS links_fts USING fts5(
    body,
    content='',
    contentless_delete=1
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_key    TEXT NOT NULL,                  -- channel_id or thread_id
    role          TEXT NOT NULL,                  -- user | assistant
    content_json  TEXT NOT NULL,                  -- raw Anthropic message content
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_thread ON conversation_turns(thread_key, created_at);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.database_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = connect()
    try:
        cur = conn.cursor()
        yield cur
    finally:
        conn.close()


def init_db() -> None:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with cursor() as cur:
        cur.executescript(SCHEMA)
        existing_cols = {
            r["name"] for r in cur.execute("PRAGMA table_info(attachments)").fetchall()
        }
        if "summary" not in existing_cols:
            cur.execute("ALTER TABLE attachments ADD COLUMN summary TEXT")
