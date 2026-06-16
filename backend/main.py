import os
import json
import threading
from typing import Optional
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

import backend.database as db
from backend.models import (
    ApplicationCreate, ApplicationUpdate, MoveStageRequest,
    SettingsUpdate, MergeRequest, ALL_STAGES,
)
from pydantic import BaseModel


class ReassignEventRequest(BaseModel):
    application_id: int


class UpdateEventStageRequest(BaseModel):
    stage: str

load_dotenv(override=True)

# Allow OAuth over http://localhost (oauthlib blocks non-HTTPS by default)
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

app = FastAPI(title="JobHunter")
db.init_db()

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "credentials/token.json")
CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials/credentials.json")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# ── Sync state (shared across requests) ───────────────────────────────────────

_sync_lock = threading.Lock()
_sync_state: dict = {
    "running": False,
    "type": None,
    "processed": 0,
    "total": 0,
    "updated": 0,
    "message": "Idle",
    "error": None,
    "result": None,
}


def _update_sync(running=None, type_=None, processed=None, total=None,
                 updated=None, message=None, error=None, result=None):
    with _sync_lock:
        if running is not None:
            _sync_state["running"] = running
        if type_ is not None:
            _sync_state["type"] = type_
        if processed is not None:
            _sync_state["processed"] = processed
        if total is not None:
            _sync_state["total"] = total
        if updated is not None:
            _sync_state["updated"] = updated
        if message is not None:
            _sync_state["message"] = message
        if error is not None:
            _sync_state["error"] = error
        if result is not None:
            _sync_state["result"] = result


def _get_sync_state() -> dict:
    with _sync_lock:
        return dict(_sync_state)


def _try_start_sync(type_: str) -> bool:
    """Atomically claim the sync slot. Checking 'running' and then setting it
    in two separate steps lets two concurrent requests both start a sync,
    doubling Gmail/Anthropic usage — so the test-and-set happens under one
    lock acquisition."""
    with _sync_lock:
        if _sync_state["running"]:
            return False
        _sync_state.update(
            running=True, type=type_, processed=0, total=0,
            updated=0, message="Starting…", error=None, result=None,
        )
        return True


# ── Static files ───────────────────────────────────────────────────────────────

@app.get("/")
def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ── Auth ───────────────────────────────────────────────────────────────────────

# Store the flow between /api/auth/url and /api/auth/callback so the PKCE
# code_verifier (generated during authorization_url) is available at token exchange.
_pending_flow = None


@app.get("/api/auth/status")
def auth_status():
    return {
        "authenticated": Path(TOKEN_FILE).exists(),
        "credentials_ready": Path(CREDENTIALS_FILE).exists(),
    }


@app.get("/api/auth/url")
def auth_url():
    global _pending_flow
    if not Path(CREDENTIALS_FILE).exists():
        raise HTTPException(400, "credentials/credentials.json not found.")
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE, scopes=SCOPES,
        redirect_uri="http://localhost:8000/api/auth/callback",
    )
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    _pending_flow = flow
    return {"url": url}


@app.get("/api/auth/callback")
def auth_callback(request: Request, code: str, state: Optional[str] = None):
    global _pending_flow
    if _pending_flow is None:
        return FileResponse(FRONTEND_DIR / "index.html")
    flow = _pending_flow
    _pending_flow = None
    # Pass the full callback URL so fetch_token can validate state and use the PKCE verifier
    flow.fetch_token(authorization_response=str(request.url))
    Path(TOKEN_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        f.write(flow.credentials.to_json())
    return FileResponse(FRONTEND_DIR / "index.html")


# ── Settings ───────────────────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings():
    return db.get_settings()


@app.patch("/api/settings")
def patch_settings(body: SettingsUpdate):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    db.update_settings(updates)
    return db.get_settings()


# ── Applications ───────────────────────────────────────────────────────────────

@app.get("/api/applications")
def list_applications(
    stage: Optional[str] = Query(None),
    company: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
):
    apps = db.list_applications(stage=stage, company=company, source=source)
    return [a for a in apps if a["company"] != "__skipped__"]


@app.post("/api/applications", status_code=201)
def create_application(body: ApplicationCreate):
    application = db.create_application(
        company=body.company, role=body.role, stage=body.stage,
        applied_date=body.applied_date.isoformat() if body.applied_date else None,
        source=body.source, notes=body.notes, job_url=body.job_url,
    )
    db.insert_stage_event(
        application_id=application["id"], stage=body.stage,
        event_type="manual", event_date=db.utc_now_iso(),
        subject="Manual entry",
    )
    return application


# Static sub-routes MUST be defined before /{app_id} parameterised routes
@app.post("/api/applications/merge")
def merge_applications(body: MergeRequest):
    if not body.merge_ids:
        raise HTTPException(400, "merge_ids must not be empty")
    if body.keep_id in body.merge_ids:
        raise HTTPException(400, "keep_id must not appear in merge_ids")
    result = db.merge_applications(body.keep_id, body.merge_ids)
    if not result:
        raise HTTPException(404, "One or more applications not found")
    return result


@app.get("/api/applications/{app_id}")
def get_application(app_id: int):
    application = db.get_application(app_id)
    if not application:
        raise HTTPException(404, "Application not found")
    return application


@app.patch("/api/applications/{app_id}")
def update_application(app_id: int, body: ApplicationUpdate):
    existing = db.get_application(app_id)
    if not existing:
        raise HTTPException(404, "Application not found")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if "applied_date" in updates and updates["applied_date"]:
        updates["applied_date"] = str(updates["applied_date"])
    stage_changed = "stage" in updates and updates["stage"] != existing["stage"]
    updated = db.update_application(app_id, **updates)
    if stage_changed:
        db.insert_stage_event(
            application_id=app_id, stage=updates["stage"],
            event_type="manual", event_date=db.utc_now_iso(),
            subject=f"Stage manually updated to {updates['stage']}",
        )
    return updated


@app.delete("/api/applications/{app_id}", status_code=204)
def delete_application(app_id: int):
    if not db.get_application(app_id):
        raise HTTPException(404, "Application not found")
    db.archive_application(app_id)


@app.get("/api/applications/{app_id}/events")
def get_events(app_id: int):
    if not db.get_application(app_id):
        raise HTTPException(404, "Application not found")
    return db.get_events(app_id)


@app.post("/api/applications/{app_id}/log-event")
def log_event(app_id: int, body: MoveStageRequest):
    """Log a manual activity. Advances the stage if appropriate; never goes backward."""
    from backend.models import can_advance
    existing = db.get_application(app_id)
    if not existing:
        raise HTTPException(404, "Application not found")
    if body.stage not in ALL_STAGES:
        raise HTTPException(400, f"Invalid stage. Valid: {ALL_STAGES}")
    if can_advance(existing["stage"], body.stage):
        db.update_application(app_id, stage=body.stage)
    db.insert_stage_event(
        application_id=app_id, stage=body.stage, event_type="manual",
        event_date=body.event_date or db.utc_now_iso(),
        subject=body.notes or f"Logged: {body.stage}",
        snippet=body.notes,
    )
    return db.get_application(app_id)


@app.post("/api/applications/{app_id}/move-stage")
def move_stage(app_id: int, body: MoveStageRequest):
    existing = db.get_application(app_id)
    if not existing:
        raise HTTPException(404, "Application not found")
    if body.stage not in ALL_STAGES:
        raise HTTPException(400, f"Invalid stage. Valid: {ALL_STAGES}")
    updated = db.update_application(app_id, stage=body.stage)
    db.insert_stage_event(
        application_id=app_id, stage=body.stage, event_type="manual",
        event_date=body.event_date or db.utc_now_iso(),
        subject=body.notes or f"Manually moved to {body.stage}",
        snippet=body.notes,
    )
    return updated


# ── Stage Events ───────────────────────────────────────────────────────────────

@app.delete("/api/events/{event_id}", status_code=204)
def delete_event(event_id: int):
    if not db.delete_event(event_id):
        raise HTTPException(404, "Event not found")


@app.patch("/api/events/{event_id}/stage")
def update_event_stage(event_id: int, body: UpdateEventStageRequest):
    if body.stage not in ALL_STAGES:
        raise HTTPException(400, f"Invalid stage. Valid: {ALL_STAGES}")
    result = db.update_event_stage(event_id, body.stage)
    if not result:
        raise HTTPException(404, "Event not found")
    return result


@app.patch("/api/events/{event_id}/reassign")
def reassign_event(event_id: int, body: ReassignEventRequest):
    result = db.reassign_event(event_id, body.application_id)
    if not result:
        raise HTTPException(404, "Event or target application not found")
    return result


# ── Sync (background threads + progress polling) ───────────────────────────────

@app.get("/api/sync/status")
def sync_status():
    return _get_sync_state()


def _friendly_error(e: Exception) -> str:
    msg = str(e)
    if "invalid_grant" in msg or "Token has been expired or revoked" in msg:
        return "Google token expired — click 'Connect Google Account' to re-authorize."
    if "invalid_client" in msg:
        return "Google OAuth client error — check credentials/credentials.json."
    if "ANTHROPIC_API_KEY" in msg or "api_key" in msg.lower():
        return "Anthropic API key missing or invalid — check your .env file."
    return msg


def _run_email_sync(gmail_label: str, min_confidence: float, ghosted_days: int):
    try:
        from backend.email_sync import sync_emails as _sync
        result = _sync(
            gmail_label=gmail_label,
            min_confidence=min_confidence,
            progress_cb=lambda processed, total, updated, msg: _update_sync(
                processed=processed, total=total, updated=updated, message=msg
            ),
        )
        ghosted = db.flag_ghosted(ghosted_days)
        result["ghosted_flagged"] = ghosted
        _update_sync(running=False, message="Done", result=result)
    except Exception as e:
        _update_sync(running=False, message="Error", error=_friendly_error(e))


def _run_calendar_sync(min_confidence: float):
    try:
        from backend.calendar_sync import sync_calendar as _sync
        result = _sync(
            min_confidence=min_confidence,
            progress_cb=lambda processed, updated, msg: _update_sync(
                processed=processed, updated=updated, message=msg
            ),
        )
        _update_sync(running=False, message="Done", result=result)
    except Exception as e:
        _update_sync(running=False, message="Error", error=_friendly_error(e))


@app.post("/api/sync/emails")
def sync_emails():
    if not Path(TOKEN_FILE).exists():
        raise HTTPException(401, "Not authenticated.")
    settings = db.get_settings()
    if not _try_start_sync("emails"):
        raise HTTPException(409, "A sync is already running.")
    t = threading.Thread(
        target=_run_email_sync,
        args=(
            settings.get("gmail_label", ""),
            float(settings.get("min_confidence", 0.6)),
            int(settings.get("ghosted_days", 30)),
        ),
        daemon=True,
    )
    t.start()
    return {"status": "started"}


@app.post("/api/sync/calendar")
def sync_calendar():
    if not Path(TOKEN_FILE).exists():
        raise HTTPException(401, "Not authenticated.")
    settings = db.get_settings()
    if not _try_start_sync("calendar"):
        raise HTTPException(409, "A sync is already running.")
    t = threading.Thread(
        target=_run_calendar_sync,
        args=(float(settings.get("min_confidence", 0.6)),),
        daemon=True,
    )
    t.start()
    return {"status": "started"}


@app.post("/api/sync/reset-email-cache")
def reset_email_cache():
    state = _get_sync_state()
    if state["running"]:
        raise HTTPException(409, "A sync is already running.")
    result = db.reset_email_cache()
    return result


# ── Stats ──────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    return db.get_stats()


@app.get("/api/stages")
def get_stages():
    return ALL_STAGES
