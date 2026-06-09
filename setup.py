#!/usr/bin/env python3
"""One-time OAuth2 setup: authenticates with Google and saves token.json."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials/credentials.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "credentials/token.json")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def main():
    if not Path(CREDENTIALS_FILE).exists():
        print(f"ERROR: {CREDENTIALS_FILE} not found.")
        print(
            "\nSetup steps:\n"
            "1. Go to https://console.cloud.google.com/\n"
            "2. Create a project → APIs & Services → Enable Gmail API + Google Calendar API\n"
            "3. OAuth consent screen → External → Audience → set Publishing status to Production\n"
            "   (Leaving it as Testing means tokens expire every 7 days)\n"
            "4. Credentials → Create OAuth client ID → Desktop app\n"
            "5. Download JSON → save as credentials/credentials.json\n"
        )
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    Path(TOKEN_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"\nSuccess! Token saved to {TOKEN_FILE}")
    print("You can now start the app: uvicorn backend.main:app --port 8000")


if __name__ == "__main__":
    main()
