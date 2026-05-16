"""Google Calendar integration.

OAuth flow lives in `scripts/google_oauth.py` (run once per user from your laptop
or via a short-lived web handler). Tokens land in the oauth_tokens table,
Fernet-encrypted with TOKEN_ENCRYPTION_KEY.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ceo_bot.config import settings
from ceo_bot.db import cursor

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def _fernet() -> Fernet:
    return Fernet(settings.token_encryption_key.encode())


def store_credentials(user_id: int, creds: Credentials) -> None:
    blob = _fernet().encrypt(creds.to_json().encode())
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO oauth_tokens (user_id, provider, token_blob, updated_at)
            VALUES (?, 'google', ?, ?)
            ON CONFLICT(user_id, provider) DO UPDATE SET
                token_blob = excluded.token_blob,
                updated_at = excluded.updated_at
            """,
            (user_id, blob, datetime.now(UTC).isoformat()),
        )


def _load_credentials(user_id: int) -> Credentials | None:
    with cursor() as cur:
        row = cur.execute(
            "SELECT token_blob FROM oauth_tokens WHERE user_id=? AND provider='google'",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    data = json.loads(_fernet().decrypt(row["token_blob"]).decode())
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        store_credentials(user_id, creds)
    return creds


async def create_event(
    *,
    user_id: int,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
    location: str = "",
) -> dict[str, Any]:
    creds = _load_credentials(user_id)
    if creds is None:
        return {
            "error": "no_google_auth",
            "message": "User has not authenticated with Google. Run /auth google in Discord.",
        }

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    body = {
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
    }
    event = service.events().insert(calendarId="primary", body=body).execute()

    with cursor() as cur:
        cur.execute(
            """
            INSERT OR IGNORE INTO calendar_events
                (provider, external_id, calendar_id, summary, start_at, end_at, raw_json, created_at)
            VALUES ('google', ?, 'primary', ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                summary,
                start_iso,
                end_iso,
                json.dumps(event),
                datetime.now(UTC).isoformat(),
            ),
        )

    return {"ok": True, "event_id": event["id"], "html_link": event.get("htmlLink")}
