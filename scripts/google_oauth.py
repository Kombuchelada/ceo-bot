"""One-time script to enroll a Discord user in Google Calendar.

Usage:
    python scripts/google_oauth.py <discord_user_id>

Prints an auth URL — open it in a browser, approve, paste the code back.
Stores the encrypted refresh token in SQLite.
"""

from __future__ import annotations

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from ceo_bot.config import settings
from ceo_bot.db import init_db
from ceo_bot.integrations.google_calendar import SCOPES, store_credentials


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python scripts/google_oauth.py <discord_user_id>", file=sys.stderr)
        sys.exit(2)
    user_id = int(sys.argv[1])

    init_db()
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"],
            }
        },
        SCOPES,
    )
    creds = flow.run_console()
    store_credentials(user_id, creds)
    print(f"stored credentials for user {user_id}")


if __name__ == "__main__":
    main()
