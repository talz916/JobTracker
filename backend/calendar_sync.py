import os
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

import backend.database as db
from backend.classifier import classify_calendar_event
from backend.models import can_advance

load_dotenv(override=True)

TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "credentials/token.json")


def _get_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    return build("calendar", "v3", credentials=creds)


def _is_likely_personal(event: dict) -> bool:
    attendees = event.get("attendees", [])
    if not attendees:
        return True
    start = event.get("start", {})
    if "date" in start and "dateTime" not in start:
        return True
    return False


def sync_calendar(min_confidence: float = 0.6, progress_cb=None) -> dict:
    service = _get_service()
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=180)).isoformat()
    time_max = (now + timedelta(days=90)).isoformat()

    processed = 0
    added = 0
    page_token = None

    while True:
        params = {
            "calendarId": "primary",
            "timeMin": time_min,
            "timeMax": time_max,
            "maxResults": 250,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if page_token:
            params["pageToken"] = page_token

        try:
            result = service.events().list(**params).execute()
        except HttpError:
            break

        for event in result.get("items", []):
            cal_event_id = event.get("id")
            if not cal_event_id or db.cal_event_exists(cal_event_id):
                continue
            if _is_likely_personal(event):
                continue

            title = event.get("summary", "")
            description = event.get("description", "") or ""
            start_dt = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")

            classification = classify_calendar_event(title, description)
            processed += 1
            if progress_cb:
                progress_cb(processed, added,
                            f"Checking calendar event {processed} — {added} updated")

            if not classification.is_job_related or classification.confidence < min_confidence:
                db.insert_stage_event(
                    application_id=db.get_or_create_placeholder(),
                    stage="other",
                    event_type="calendar",
                    calendar_event_id=cal_event_id,
                )
                continue

            company = classification.company or "Unknown Company"
            app = db.find_application_by_company(company)
            if app is None:
                app = db.create_application(
                    company=company,
                    role=classification.role,
                    stage=classification.stage,
                    applied_date=start_dt[:10] if start_dt else None,
                    source="email",
                    notes=None,
                    job_url=None,
                )
            else:
                if can_advance(app["stage"], classification.stage):
                    db.update_application(app["id"], stage=classification.stage)
                    app = db.get_application(app["id"])

            db.insert_stage_event(
                application_id=app["id"],
                stage=classification.stage,
                event_type="calendar",
                event_date=start_dt,
                subject=title,
                calendar_event_id=cal_event_id,
                snippet=description[:200],
            )
            added += 1

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return {"events_checked": processed, "applications_updated": added}
