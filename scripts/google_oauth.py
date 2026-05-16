"""One-time script to enroll a Discord user in Google Calendar.

Usage:
    python scripts/google_oauth.py <discord_user_id>

Prints an auth URL. Open it in a browser, approve, then copy the FULL
redirect URL (the one the browser tries to load after you approve — it
will show a "can't reach localhost" error, that's fine) and paste it
back here. Stores the encrypted refresh token in SQLite.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

from google_auth_oauthlib.flow import InstalledAppFlow

from ceo_bot.config import settings
from ceo_bot.db import init_db
from ceo_bot.integrations.google_calendar import SCOPES, store_credentials


REDIRECT_URI = "http://localhost"


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
                "redirect_uris": [REDIRECT_URI],
            }
        },
        SCOPES,
    )
    flow.redirect_uri = REDIRECT_URI

    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    print("\nOpen this URL in your browser and approve:\n")
    print(auth_url)
    print("\nAfter approving, your browser will try to load a localhost URL")
    print("and show an error — that's expected. Copy the FULL URL from the")
    print("address bar (it contains the auth code) and paste it below.\n")
    redirect_response = input("Pasted URL: ").strip()

    flow.fetch_token(authorization_response=redirect_response)
    store_credentials(user_id, flow.credentials)
    print(f"stored credentials for user {user_id}")


if __name__ == "__main__":
    main()
