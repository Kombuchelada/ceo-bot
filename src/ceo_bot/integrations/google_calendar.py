"""Google Calendar integration.

OAuth flow lives in `scripts/google_oauth.py` (run once per user from your laptop
or via a short-lived web handler). Tokens land in the oauth_tokens table,
Fernet-encrypted with TOKEN_ENCRYPTION_KEY.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog
from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ceo_bot.config import settings
from ceo_bot.db import cursor

log = structlog.get_logger()

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


def _insert_event(
    creds: Credentials,
    *,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str,
    location: str,
    attendees: list[dict[str, str]],
    send_updates: str,
) -> dict[str, Any]:
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    body: dict[str, Any] = {
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
    }
    if attendees:
        body["attendees"] = attendees
    return (
        service.events()
        .insert(calendarId="primary", body=body, sendUpdates=send_updates)
        .execute()
    )


async def create_event(
    *,
    user_id: int,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
    location: str = "",
    attendee_user_ids: list[int] | None = None,
) -> dict[str, Any]:
    requester_creds = _load_credentials(user_id)
    if requester_creds is None:
        return {
            "error": "no_google_auth",
            "message": "Requester has not authenticated with Google. Run /auth google in Discord.",
        }

    members = settings.household_by_id
    attendee_user_ids = [uid for uid in (attendee_user_ids or []) if uid != user_id]

    # Partition attendees: direct-write (have OAuth) vs email-invite (no OAuth).
    direct_writes: list[tuple[int, Credentials]] = []
    email_invites: list[str] = []
    unknown_ids: list[int] = []

    for uid in attendee_user_ids:
        creds = _load_credentials(uid)
        if creds is not None:
            direct_writes.append((uid, creds))
        else:
            member = members.get(uid)
            if member is None:
                unknown_ids.append(uid)
            else:
                email_invites.append(member.email)

    # Email-invite attendees ride on the requester's event so they get notified.
    attendees_body = [{"email": e} for e in email_invites]
    send_updates_for_requester = "all" if email_invites else "none"

    now_iso = datetime.now(UTC).isoformat()
    results: list[dict[str, Any]] = []

    # 1. Requester's calendar — always first, carries the email invites.
    try:
        event = _insert_event(
            requester_creds,
            summary=summary,
            start_iso=start_iso,
            end_iso=end_iso,
            description=description,
            location=location,
            attendees=attendees_body,
            send_updates=send_updates_for_requester,
        )
    except Exception as exc:
        log.exception("calendar.insert_failed", user_id=user_id)
        return {"error": "insert_failed", "message": str(exc)}

    results.append(
        {
            "user_id": user_id,
            "event_id": event["id"],
            "html_link": event.get("htmlLink"),
        }
    )
    with cursor() as cur:
        cur.execute(
            """
            INSERT OR IGNORE INTO calendar_events
                (provider, external_id, calendar_id, summary, start_at, end_at, raw_json, created_at)
            VALUES ('google', ?, 'primary', ?, ?, ?, ?, ?)
            """,
            (event["id"], summary, start_iso, end_iso, json.dumps(event), now_iso),
        )

    # 2. Direct-write attendees — bare event on each of their calendars, no attendees
    #    field (avoids Google's auto-add behavior duplicating with the requester's copy).
    for uid, creds in direct_writes:
        try:
            mirror = _insert_event(
                creds,
                summary=summary,
                start_iso=start_iso,
                end_iso=end_iso,
                description=description,
                location=location,
                attendees=[],
                send_updates="none",
            )
        except Exception as exc:
            log.exception("calendar.mirror_failed", user_id=uid)
            results.append({"user_id": uid, "error": str(exc)})
            continue
        results.append(
            {
                "user_id": uid,
                "event_id": mirror["id"],
                "html_link": mirror.get("htmlLink"),
            }
        )
        with cursor() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO calendar_events
                    (provider, external_id, calendar_id, summary, start_at, end_at, raw_json, created_at)
                VALUES ('google', ?, 'primary', ?, ?, ?, ?, ?)
                """,
                (mirror["id"], summary, start_iso, end_iso, json.dumps(mirror), now_iso),
            )

    return {
        "ok": True,
        "events": results,
        "html_link": results[0].get("html_link"),
        "email_invited": email_invites,
        "unknown_attendee_ids": unknown_ids,
    }
