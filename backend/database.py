import sqlite3
import os
from typing import Optional, List
from datetime import datetime, timedelta, date as date_type
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv(override=True)

DB_PATH = os.getenv("DATABASE_PATH", "jobhunter.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    role TEXT,
    stage TEXT NOT NULL DEFAULT 'applied',
    applied_date DATE,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
    source TEXT DEFAULT 'email',
    notes TEXT,
    job_url TEXT
);

CREATE TABLE IF NOT EXISTS stage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_date DATETIME,
    subject TEXT,
    thread_id TEXT,
    message_id TEXT,
    calendar_event_id TEXT,
    snippet TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cal_event
    ON stage_events(calendar_event_id) WHERE calendar_event_id IS NOT NULL;
"""

DEFAULT_SETTINGS = {
    "gmail_label": "",
    "ghosted_days": "30",
    "min_confidence": "0.6",
}

# Internal record that caches threads/events classified as not job-related.
# Hidden from the UI; may be deleted by reset_email_cache, so callers must
# always go through get_or_create_placeholder() instead of caching its id.
PLACEHOLDER_COMPANY = "__skipped__"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        # Migrate: recruiter_outreach removed — fold into applied
        conn.execute(
            "UPDATE applications SET stage = 'applied' WHERE stage = 'recruiter_outreach'"
        )
        conn.execute(
            "UPDATE stage_events SET stage = 'applied' WHERE stage = 'recruiter_outreach'"
        )
        # Migrate: add archived column for soft-delete
        try:
            conn.execute(
                "ALTER TABLE applications ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
        except Exception:
            pass  # Column already exists
        # Migrate: track individual messages within a thread so replies
        # (rejections, interview invites) update the stage. The old index was
        # unique on thread_id alone, which locked a thread after its first
        # message forever.
        try:
            conn.execute("ALTER TABLE stage_events ADD COLUMN message_id TEXT")
        except Exception:
            pass  # Column already exists
        conn.execute("DROP INDEX IF EXISTS idx_thread")
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_thread_msg
               ON stage_events(thread_id, message_id) WHERE thread_id IS NOT NULL"""
        )


# ── Settings ──────────────────────────────────────────────────────────────────

def get_settings() -> dict:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = dict(DEFAULT_SETTINGS)
    settings.update({r["key"]: r["value"] for r in rows})
    return settings


def update_settings(updates: dict):
    with get_db() as conn:
        for key, value in updates.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, str(value)),
            )


# ── Applications ───────────────────────────────────────────────────────────────

def create_application(company: str, role: Optional[str], stage: str,
                        applied_date, source: str, notes: Optional[str],
                        job_url: Optional[str]) -> dict:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO applications
               (company, role, stage, applied_date, source, notes, job_url)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (company, role, stage, applied_date, source, notes, job_url),
        )
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def get_application(app_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        return dict(row) if row else None


def find_application_by_company(company: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE lower(company) = lower(?) AND archived = 0",
            (company,),
        ).fetchone()
        return dict(row) if row else None


def find_application_by_company_and_role(company: str, role: str) -> Optional[dict]:
    """Match by both company and role (case-insensitive). Returns None if no match."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM applications
               WHERE lower(company) = lower(?) AND lower(role) = lower(?) AND archived = 0""",
            (company, role),
        ).fetchone()
        return dict(row) if row else None


def find_oldest_active_application_by_company(company: str) -> Optional[dict]:
    """Returns the oldest non-terminal application for a company (for role-ambiguous emails)."""
    terminal = ('rejected', 'declined_offer', 'ghosted')
    terminal_ph = ','.join('?' * len(terminal))
    with get_db() as conn:
        row = conn.execute(
            f"""SELECT * FROM applications
               WHERE lower(company) = lower(?) AND archived = 0
               AND stage NOT IN ({terminal_ph})
               ORDER BY applied_date ASC, id ASC LIMIT 1""",
            (company, *terminal),
        ).fetchone()
        return dict(row) if row else None


def list_applications(stage: Optional[str] = None, company: Optional[str] = None,
                      source: Optional[str] = None) -> List[dict]:
    query = "SELECT * FROM applications WHERE archived = 0"
    params = []
    if stage:
        query += " AND stage = ?"
        params.append(stage)
    if company:
        query += " AND lower(company) LIKE lower(?)"
        params.append(f"%{company}%")
    if source:
        query += " AND source = ?"
        params.append(source)
    query += " ORDER BY last_updated DESC"
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def update_application(app_id: int, **fields) -> Optional[dict]:
    if not fields:
        return get_application(app_id)
    fields["last_updated"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [app_id]
    with get_db() as conn:
        conn.execute(
            f"UPDATE applications SET {set_clause} WHERE id = ?", values
        )
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        return dict(row) if row else None


def archive_application(app_id: int):
    """Soft-delete: hides the application from the UI and prevents re-creation on sync."""
    with get_db() as conn:
        conn.execute(
            "UPDATE applications SET archived = 1, last_updated = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), app_id),
        )


def delete_application(app_id: int):
    """Hard-delete — only used internally (e.g. merge). Use archive_application from API routes."""
    with get_db() as conn:
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))


def merge_applications(keep_id: int, merge_ids: List[int]) -> Optional[dict]:
    """Move all stage events from merge_ids into keep_id, then delete the duplicates.
    The kept record is updated to the most advanced stage across all merged apps."""
    from backend.models import STAGE_RANK, TERMINAL_STAGES
    all_ids = [keep_id] + merge_ids
    with get_db() as conn:
        # Fetch all apps being merged
        placeholders = ",".join("?" * len(all_ids))
        rows = conn.execute(
            f"SELECT * FROM applications WHERE id IN ({placeholders})", all_ids
        ).fetchall()
        apps = [dict(r) for r in rows]
        if len(apps) < 2:
            return None

        # Determine best stage: terminal stages take priority, otherwise highest rank
        def stage_priority(stage):
            if stage in TERMINAL_STAGES:
                return (1, 0)
            rank = STAGE_RANK.get(stage)
            return (0, rank or 0)

        best_stage = max((a["stage"] for a in apps), key=stage_priority)

        # Reassign stage_events — skip unique constraint conflicts (duplicate thread/cal IDs)
        for merge_id in merge_ids:
            conn.execute(
                "UPDATE OR IGNORE stage_events SET application_id = ? WHERE application_id = ?",
                (keep_id, merge_id),
            )
            # Delete any events that couldn't be moved (duplicate thread_id/cal_event_id)
            conn.execute("DELETE FROM stage_events WHERE application_id = ?", (merge_id,))

        # Update kept record with best stage and merge notes
        conn.execute(
            "UPDATE applications SET stage = ?, last_updated = ? WHERE id = ?",
            (best_stage, datetime.utcnow().isoformat(), keep_id),
        )

        # Delete the merged duplicates
        merge_placeholders = ",".join("?" * len(merge_ids))
        conn.execute(
            f"DELETE FROM applications WHERE id IN ({merge_placeholders})", merge_ids
        )

        # Log the merge as a manual event
        merged_companies = ", ".join(a["company"] for a in apps if a["id"] != keep_id)
        conn.execute(
            """INSERT INTO stage_events
               (application_id, stage, event_type, event_date, subject)
               VALUES (?, ?, 'manual', ?, ?)""",
            (keep_id, best_stage, datetime.utcnow().isoformat(),
             f"Merged duplicates: {merged_companies}"),
        )

        row = conn.execute("SELECT * FROM applications WHERE id = ?", (keep_id,)).fetchone()
        return dict(row) if row else None


def get_or_create_placeholder() -> int:
    """Return the id of the '__skipped__' placeholder application, creating it
    if missing. Looked up on every call: reset_email_cache can delete the
    placeholder, so a cached id would point at a dead row and every subsequent
    stage-event insert would fail its foreign-key check."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM applications WHERE company = ?", (PLACEHOLDER_COMPANY,)
        ).fetchone()
        if row:
            return row["id"]
    app = create_application(
        company=PLACEHOLDER_COMPANY, role=None, stage="other",
        applied_date=None, source="auto",
        notes="Internal placeholder for non-job-related items", job_url=None,
    )
    return app["id"]


# ── Stage Events ───────────────────────────────────────────────────────────────

def get_events(app_id: int) -> List[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM stage_events WHERE application_id = ? ORDER BY event_date, created_at",
            (app_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def thread_exists(thread_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM stage_events WHERE thread_id = ?", (thread_id,)
        ).fetchone()
    return row is not None


def thread_message_ids(thread_id: str) -> set:
    """All Gmail message ids already recorded for a thread. May contain None
    for rows written before message-level tracking existed."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT message_id FROM stage_events WHERE thread_id = ?", (thread_id,)
        ).fetchall()
    return {r["message_id"] for r in rows}


def backfill_thread_message_id(thread_id: str, message_id: str):
    """Stamp pre-migration rows (message_id NULL) with the thread's first
    message id so the thread isn't endlessly re-classified after upgrade."""
    with get_db() as conn:
        conn.execute(
            "UPDATE stage_events SET message_id = ? WHERE thread_id = ? AND message_id IS NULL",
            (message_id, thread_id),
        )


def cal_event_exists(cal_event_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM stage_events WHERE calendar_event_id = ?", (cal_event_id,)
        ).fetchone()
    return row is not None


def insert_stage_event(application_id: int, stage: str, event_type: str,
                        event_date=None, subject: Optional[str] = None,
                        thread_id: Optional[str] = None,
                        calendar_event_id: Optional[str] = None,
                        snippet: Optional[str] = None,
                        message_id: Optional[str] = None):
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO stage_events
               (application_id, stage, event_type, event_date, subject,
                thread_id, message_id, calendar_event_id, snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (application_id, stage, event_type, event_date, subject,
             thread_id, message_id, calendar_event_id, snippet),
        )


def delete_event(event_id: int) -> bool:
    """Delete a stage_event and recalculate the parent application's stage."""
    from backend.models import STAGE_RANK, TERMINAL_STAGES

    def _best_stage(stages):
        def priority(s):
            return (1, 0) if s in TERMINAL_STAGES else (0, STAGE_RANK.get(s, 0))
        return max(stages, key=priority)

    with get_db() as conn:
        event = conn.execute(
            "SELECT * FROM stage_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not event:
            return False

        app_id = event["application_id"]
        conn.execute("DELETE FROM stage_events WHERE id = ?", (event_id,))

        remaining = [
            r["stage"] for r in conn.execute(
                "SELECT stage FROM stage_events WHERE application_id = ?", (app_id,)
            ).fetchall()
        ]
        if remaining:
            conn.execute(
                "UPDATE applications SET stage = ?, last_updated = ? WHERE id = ?",
                (_best_stage(remaining), datetime.utcnow().isoformat(), app_id),
            )
        return True


def update_event_stage(event_id: int, stage: str) -> Optional[dict]:
    """Update a stage_event's stage and recalculate the parent application's stage."""
    from backend.models import STAGE_RANK, TERMINAL_STAGES

    def _best_stage(stages):
        def priority(s):
            return (1, 0) if s in TERMINAL_STAGES else (0, STAGE_RANK.get(s, 0))
        return max(stages, key=priority)

    with get_db() as conn:
        event = conn.execute(
            "SELECT * FROM stage_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not event:
            return None

        conn.execute(
            "UPDATE stage_events SET stage = ? WHERE id = ?",
            (stage, event_id),
        )

        # Recalculate application stage from all its events
        all_stages = [
            r["stage"] for r in conn.execute(
                "SELECT stage FROM stage_events WHERE application_id = ?",
                (event["application_id"],),
            ).fetchall()
        ]
        if all_stages:
            best = _best_stage(all_stages)
            conn.execute(
                "UPDATE applications SET stage = ?, last_updated = ? WHERE id = ?",
                (best, datetime.utcnow().isoformat(), event["application_id"]),
            )

        row = conn.execute(
            "SELECT * FROM stage_events WHERE id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None


def reassign_event(event_id: int, target_app_id: int) -> Optional[dict]:
    """Move a stage_event to a different application and recalculate both apps' stages."""
    from backend.models import STAGE_RANK, TERMINAL_STAGES

    def _best_stage(stages):
        def priority(s):
            return (1, 0) if s in TERMINAL_STAGES else (0, STAGE_RANK.get(s, 0))
        return max(stages, key=priority)

    with get_db() as conn:
        event = conn.execute(
            "SELECT * FROM stage_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not event:
            return None

        source_app_id = event["application_id"]

        target_app = conn.execute(
            "SELECT id FROM applications WHERE id = ? AND archived = 0", (target_app_id,)
        ).fetchone()
        if not target_app:
            return None

        conn.execute(
            "UPDATE stage_events SET application_id = ? WHERE id = ?",
            (target_app_id, event_id),
        )

        # Recalculate source app stage from its remaining events
        if source_app_id != target_app_id:
            src_stages = [
                r["stage"] for r in conn.execute(
                    "SELECT stage FROM stage_events WHERE application_id = ?",
                    (source_app_id,),
                ).fetchall()
            ]
            if src_stages:
                conn.execute(
                    "UPDATE applications SET stage = ?, last_updated = ? WHERE id = ?",
                    (_best_stage(src_stages), datetime.utcnow().isoformat(), source_app_id),
                )

        # Recalculate target app stage
        tgt_stages = [
            r["stage"] for r in conn.execute(
                "SELECT stage FROM stage_events WHERE application_id = ?",
                (target_app_id,),
            ).fetchall()
        ]
        if tgt_stages:
            conn.execute(
                "UPDATE applications SET stage = ?, last_updated = ? WHERE id = ?",
                (_best_stage(tgt_stages), datetime.utcnow().isoformat(), target_app_id),
            )

        return {"event_id": event_id, "application_id": target_app_id}


# ── Stats ──────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    funnel_stages = [
        "applied", "phone_screen", "interview",
        "technical_assessment", "hr_interview", "offer",
    ]
    funnel_ph = ",".join("?" * len(funnel_stages))
    interview_stages = ["interview", "technical_assessment", "hr_interview", "offer"]
    interview_ph = ",".join("?" * len(interview_stages))

    with get_db() as conn:
        rows = conn.execute(
            "SELECT stage, COUNT(*) as count FROM applications WHERE archived = 0 GROUP BY stage"
        ).fetchall()
        reached_rows = conn.execute(
            f"""SELECT se.stage, COUNT(DISTINCT se.application_id) as count
               FROM stage_events se
               JOIN applications a ON a.id = se.application_id
               WHERE a.archived = 0 AND a.company != '__skipped__'
               AND se.stage IN ({funnel_ph})
               GROUP BY se.stage""",
            funnel_stages,
        ).fetchall()
        referral_rows = conn.execute(
            f"""SELECT a.source, COUNT(DISTINCT se.application_id) as count
               FROM stage_events se
               JOIN applications a ON a.id = se.application_id
               WHERE a.archived = 0 AND a.company != '__skipped__'
               AND se.stage IN ({interview_ph})
               GROUP BY a.source""",
            interview_stages,
        ).fetchall()
        weekly_raw = conn.execute(
            """SELECT
                 date(applied_date,
                   '-' || ((cast(strftime('%w', applied_date) as integer) + 6) % 7) || ' days'
                 ) as week_monday,
                 COUNT(*) as count
               FROM applications
               WHERE archived = 0 AND applied_date IS NOT NULL AND company != '__skipped__'
               GROUP BY week_monday""",
        ).fetchall()

    counts = {r["stage"]: r["count"] for r in rows}
    counts.pop("__skipped__", None)
    counts.pop("other", None)
    total = sum(counts.values())

    reached = {r["stage"]: r["count"] for r in reached_rows}
    reached["applied"] = total  # every application starts as applied

    screen_to_interview_rate = (
        round(reached.get("interview", 0) / reached["phone_screen"] * 100, 1)
        if reached.get("phone_screen", 0) > 0 else None
    )

    interview_by_source = {r["source"]: r["count"] for r in referral_rows}
    total_interviews_reached = sum(interview_by_source.values())
    referral_interview_rate = (
        round(interview_by_source.get("referral", 0) / total_interviews_reached * 100, 1)
        if total_interviews_reached > 0 else None
    )

    weekly_map = {r["week_monday"]: r["count"] for r in weekly_raw}
    today = date_type.today()
    this_monday = today - timedelta(days=today.weekday())
    weekly_volume = []
    for i in range(11, -1, -1):
        monday = this_monday - timedelta(weeks=i)
        week_str = monday.isoformat()
        weekly_volume.append({"week": week_str, "count": weekly_map.get(week_str, 0)})

    ordered = [
        "applied", "recruiter_outreach", "phone_screen", "interview",
        "technical_assessment", "hr_interview", "offer",
        "rejected", "declined_offer", "ghosted",
    ]
    funnel = []
    for s in ordered:
        n = counts.get(s, 0)
        funnel.append({"stage": s, "count": n,
                        "pct_of_total": round(n / total * 100, 1) if total else 0})

    active = total - counts.get("rejected", 0) - counts.get("declined_offer", 0) - counts.get("ghosted", 0)

    def rate(stage, decimals=1):
        return round(counts.get(stage, 0) / total * 100, decimals) if total else 0

    return {
        "total": total,
        "active": active,
        "phone_screen_rate": round(reached.get("phone_screen", 0) / total * 100, 1) if total else 0,
        "interview_rate": round(reached.get("interview", 0) / total * 100, 1) if total else 0,
        "offer_rate": round(reached.get("offer", 0) / total * 100, 2) if total else 0,
        "rejection_rate": rate("rejected"),
        "screen_to_interview_rate": screen_to_interview_rate,
        "referral_interview_rate": referral_interview_rate,
        "funnel": funnel,
        "by_stage": counts,
        "reached_by_stage": reached,
        "weekly_volume": weekly_volume,
    }


# ── Email cache reset ─────────────────────────────────────────────────────────

def reset_email_cache() -> dict:
    """Delete all email-sourced stage events so threads are re-evaluated on next sync."""
    with get_db() as conn:
        # Count before deletion
        deleted_events = conn.execute(
            "SELECT COUNT(*) FROM stage_events WHERE event_type = 'email'"
        ).fetchone()[0]

        conn.execute("DELETE FROM stage_events WHERE event_type = 'email'")

        # Remove applications that are now event-less and were auto-created from email
        orphaned = conn.execute(
            """SELECT id FROM applications
               WHERE source IN ('email', 'referral', 'auto')
               AND id NOT IN (SELECT DISTINCT application_id FROM stage_events)"""
        ).fetchall()
        orphan_ids = [r["id"] for r in orphaned]
        if orphan_ids:
            conn.execute(
                f"DELETE FROM applications WHERE id IN ({','.join('?' * len(orphan_ids))})",
                orphan_ids,
            )

    return {"events_cleared": deleted_events, "applications_removed": len(orphan_ids)}


# ── Ghosted auto-detection ─────────────────────────────────────────────────────

def flag_ghosted(ghosted_days: int = 30) -> int:
    cutoff = (datetime.utcnow() - timedelta(days=ghosted_days)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id FROM applications
               WHERE stage IN ('applied', 'recruiter_outreach')
               AND archived = 0
               AND last_updated < ?""",
            (cutoff,),
        ).fetchall()
        for row in rows:
            app_id = row["id"]
            conn.execute(
                "UPDATE applications SET stage = 'ghosted', last_updated = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), app_id),
            )
            conn.execute(
                """INSERT OR IGNORE INTO stage_events
                   (application_id, stage, event_type, event_date, subject)
                   VALUES (?, 'ghosted', 'auto', ?, 'Auto-flagged as ghosted')""",
                (app_id, datetime.utcnow().isoformat()),
            )
    return len(rows)
