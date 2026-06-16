import base64
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Set
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

import backend.database as db
from backend.classifier import classify_email
from backend.models import can_advance

load_dotenv(override=True)

TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "credentials/token.json")

KEYWORD_QUERIES = [
    "subject:(application received OR thank you for applying OR application confirmed OR we received your application)",
    "subject:(interview OR phone screen OR screening call) (job OR role OR position OR opportunity)",
    "subject:(unfortunately OR we regret OR not moving forward OR other candidates)",
    "subject:(offer letter OR pleased to offer OR congratulations)",
    "subject:(technical assessment OR take-home assignment OR coding challenge OR home assignment)",
    "subject:(opportunity OR position OR role) from:(comeet-notifications.com OR greenhouse.io OR lever.co OR ashbyhq.com OR jobvite.com)",
]


def _get_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    return build("gmail", "v1", credentials=creds)


def _html_to_text(html: str) -> str:
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&#\d+;', ' ', text)
    return re.sub(r'[ \t]{2,}', ' ', text).strip()


def _decode_part(part: dict) -> tuple:
    """Return (text, mime_type) for a single part."""
    data = part.get("body", {}).get("data", "")
    if not data:
        return "", ""
    raw = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return raw, part.get("mimeType", "")


def _decode_body(payload: dict) -> str:
    mime = payload.get("mimeType", "")

    # Single-part plain text
    if mime == "text/plain":
        text, _ = _decode_part(payload)
        if text:
            return text

    # Single-part HTML (HTML-only emails like Comeet / Spark Hire)
    if mime == "text/html":
        html, _ = _decode_part(payload)
        if html:
            return _html_to_text(html)

    # Multipart: prefer text/plain, fall back to text/html
    plain = html_fallback = ""
    for part in payload.get("parts", []):
        part_mime = part.get("mimeType", "")
        if part_mime == "text/plain":
            raw, _ = _decode_part(part)
            if raw:
                plain = raw
        elif part_mime == "text/html" and not html_fallback:
            raw, _ = _decode_part(part)
            if raw:
                html_fallback = _html_to_text(raw)
        else:
            # Recurse into nested multipart (e.g. multipart/alternative inside multipart/mixed)
            nested = _decode_body(part)
            if nested and not plain:
                plain = nested

    return plain or html_fallback


def _get_header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _fetch_thread_ids(service, query: str, max_results: int = 500) -> list:
    ids = []
    page_token = None
    while True:
        params = {"userId": "me", "q": query, "maxResults": min(50, max_results - len(ids))}
        if page_token:
            params["pageToken"] = page_token
        result = service.users().threads().list(**params).execute()
        for t in result.get("threads", []):
            ids.append(t["id"])
        page_token = result.get("nextPageToken")
        if not page_token or len(ids) >= max_results:
            break
    return ids


# Workers fetch and classify in parallel, but application match-or-create must
# be serialized: two emails from the same company processed at the same time
# would otherwise both see "no application yet" and create duplicates.
_db_write_lock = threading.Lock()


def _parse_email_date(date_str: str) -> Optional[str]:
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_str).isoformat() if date_str else None
    except Exception:
        return None


def _process_thread(service, thread_id: str, min_confidence: float,
                    known_ids: Optional[set] = None) -> Optional[dict]:
    """Classify the newest unseen message in a thread.

    Recruiting threads keep evolving — the rejection or interview invite
    usually arrives as a reply to the confirmation email — so a thread is
    never 'done': whenever it grows, the newest message is classified and
    can advance the application's stage."""
    # Cheap metadata fetch first: just enough to see whether the newest
    # message is one we haven't classified yet.
    try:
        thread = service.users().threads().get(
            userId="me", id=thread_id, format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
    except HttpError:
        return None

    messages = thread.get("messages", [])
    if not messages:
        return None

    newest_id = messages[-1]["id"]
    if known_ids is None:
        known_ids = db.thread_message_ids(thread_id)
    if newest_id in known_ids:
        return None  # nothing new in this thread

    if None in known_ids:
        # Rows from before message-level tracking: stamp them with the first
        # message's id so the thread isn't re-classified forever.
        db.backfill_thread_message_id(thread_id, messages[0]["id"])
        if len(messages) == 1:
            return None  # the legacy row already covers the only message

    try:
        msg = service.users().messages().get(
            userId="me", id=newest_id, format="full"
        ).execute()
    except HttpError:
        return None

    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    sender = _get_header(headers, "From")
    subject = _get_header(headers, "Subject")
    body = _decode_body(payload)[:1500]
    event_date = _parse_email_date(_get_header(headers, "Date"))

    result = classify_email(sender, subject, body)

    # Everything below mutates the DB based on a read — serialize it so
    # parallel workers can't race on match-or-create.
    with _db_write_lock:
        if not result.is_job_related or result.confidence < min_confidence:
            db.insert_stage_event(
                application_id=_get_or_create_placeholder(),
                stage="other",
                event_type="email",
                thread_id=thread_id,
                message_id=newest_id,
            )
            return None

        company = result.company or "Unknown Company"
        source = "referral" if result.is_referral else "email"

        # Match by (company, role) when role is known; otherwise route to oldest active position
        if result.role:
            app = db.find_application_by_company_and_role(company, result.role)
        else:
            app = db.find_oldest_active_application_by_company(company)

        if app is None:
            # applied_date comes from the thread's first message (the original
            # application/confirmation), not the message being classified now.
            first_headers = messages[0].get("payload", {}).get("headers", [])
            applied = _parse_email_date(_get_header(first_headers, "Date")) or event_date
            app = db.create_application(
                company=company,
                role=result.role,
                stage=result.stage,
                applied_date=applied[:10] if applied else None,
                source=source,
                notes=None,
                job_url=None,
            )
        else:
            if result.is_referral and app["source"] == "email":
                db.update_application(app["id"], source="referral")
            if can_advance(app["stage"], result.stage):
                db.update_application(app["id"], stage=result.stage)
                app = db.get_application(app["id"])

        db.insert_stage_event(
            application_id=app["id"],
            stage=result.stage,
            event_type="email",
            event_date=event_date,
            subject=subject,
            thread_id=thread_id,
            message_id=newest_id,
            snippet=body[:200],
        )
    return {"company": company, "stage": result.stage, "thread_id": thread_id}


_placeholder_id: Optional[int] = None


def _get_or_create_placeholder() -> int:
    global _placeholder_id
    if _placeholder_id is not None:
        return _placeholder_id
    app = db.find_application_by_company("__skipped__")
    if app is None:
        app = db.create_application(
            company="__skipped__",
            role=None,
            stage="other",
            applied_date=None,
            source="auto",
            notes="Internal placeholder for non-job-related threads",
            job_url=None,
        )
    _placeholder_id = app["id"]
    return _placeholder_id


def sync_emails(gmail_label: str = "", min_confidence: float = 0.6,
                progress_cb=None, service=None, max_workers: int = 6) -> dict:
    # The Gmail client is built on httplib2, which is NOT thread-safe: sharing
    # one service across worker threads corrupts the TLS stream and fails with
    # '[SSL: WRONG_VERSION_NUMBER]'. So each worker thread gets its own client.
    # When a service is injected (tests), reuse it as-is — fakes have no sockets.
    injected = service is not None
    _tlocal = threading.local()

    def worker_service():
        if injected:
            return service
        svc = getattr(_tlocal, "svc", None)
        if svc is None:
            svc = _get_service()
            _tlocal.svc = svc
        return svc

    service = service or _get_service()  # used for the sequential thread-id collection
    queries = list(KEYWORD_QUERIES)
    if gmail_label:
        queries.insert(0, f'label:"{gmail_label}"')

    # Collect all thread IDs first so we know the total
    if progress_cb:
        progress_cb(0, 0, 0, "Collecting threads from Gmail…")

    seen_ids: Set[str] = set()
    all_ids = []
    for query in queries:
        try:
            thread_ids = _fetch_thread_ids(service, query)
        except HttpError:
            continue
        for tid in thread_ids:
            if tid not in seen_ids:
                seen_ids.add(tid)
                all_ids.append(tid)

    total = len(all_ids)

    if progress_cb:
        progress_cb(0, total, 0, f"Found {total} threads to check…")

    # One query for everything we've already recorded, instead of one per thread
    known = db.all_thread_message_ids()

    progress_lock = threading.Lock()
    counters = {"processed": 0, "added": 0}

    def _work(tid: str):
        result = _process_thread(worker_service(), tid, min_confidence,
                                 known_ids=known.get(tid))
        with progress_lock:
            counters["processed"] += 1
            if result:
                counters["added"] += 1
            processed, added = counters["processed"], counters["added"]
        if progress_cb and (processed % 5 == 0 or processed == total):
            progress_cb(processed, total, added,
                        f"Checking {processed}/{total} threads — {added} applications updated")

    # The slow parts (Gmail fetch + Claude classification) run in parallel;
    # DB mutations are serialized by _db_write_lock inside _process_thread.
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_work, all_ids))

    return {"threads_checked": counters["processed"],
            "applications_updated": counters["added"]}
