"""Timestamps used to be stored in three formats — SQLite CURRENT_TIMESTAMP
('YYYY-MM-DD HH:MM:SS'), naive isoformat(), and email dates with arbitrary
timezone offsets — and compared as strings, breaking ghosted-detection
cutoffs and event ordering. Everything now goes through one canonical
naive-UTC ISO format, with a startup migration for existing rows."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.database as database


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    return database


def test_utc_now_iso_shape():
    value = database.utc_now_iso()
    assert "T" in value
    assert "+" not in value and not value.endswith("Z")
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is None
    assert abs((datetime.now(timezone.utc).replace(tzinfo=None) - parsed).total_seconds()) < 5


@pytest.mark.parametrize("raw,expected", [
    ("2026-06-11 13:43:00", "2026-06-11T13:43:00"),          # SQLite CURRENT_TIMESTAMP
    ("2026-06-11T13:43:00", "2026-06-11T13:43:00"),          # already canonical
    ("2026-06-11T15:43:00+02:00", "2026-06-11T13:43:00"),    # email date with offset
    ("2026-06-11T13:43:00Z", "2026-06-11T13:43:00"),         # calendar RFC3339 Zulu
    ("2026-06-11", "2026-06-11"),                            # all-day event, untouched
    ("not a date", None),
    (None, None),
    ("", None),
])
def test_to_utc_iso(raw, expected):
    assert database.to_utc_iso(raw) == expected


def test_to_utc_iso_datetime_objects():
    aware = datetime(2026, 6, 11, 15, 43, tzinfo=timezone(timedelta(hours=2)))
    assert database.to_utc_iso(aware) == "2026-06-11T13:43:00"
    naive = datetime(2026, 6, 11, 13, 43)
    assert database.to_utc_iso(naive) == "2026-06-11T13:43:00"


def test_migration_normalizes_legacy_rows(db):
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO applications (company, stage, last_updated)
               VALUES ('Acme', 'applied', '2026-05-01 10:00:00')"""
        )
        app_id = conn.execute("SELECT id FROM applications").fetchone()["id"]
        conn.execute(
            """INSERT INTO stage_events
               (application_id, stage, event_type, event_date, created_at)
               VALUES (?, 'applied', 'email',
                       '2026-05-01T12:00:00+02:00', '2026-05-01 10:00:00')""",
            (app_id,),
        )

    db.init_db()  # runs the normalization migration

    app = db.get_application(app_id)
    assert app["last_updated"] == "2026-05-01T10:00:00"
    event = db.get_events(app_id)[0]
    assert event["event_date"] == "2026-05-01T10:00:00"  # +02:00 folded into UTC
    assert event["created_at"] == "2026-05-01T10:00:00"


def test_event_ordering_across_formats(db):
    """Events written with offset formats used to sort by offset string, not
    by actual time. After normalization the real chronology wins."""
    app = db.create_application(company="Acme", role=None, stage="applied",
                                applied_date=None, source="email", notes=None,
                                job_url=None)
    # 11:00 UTC expressed with +02:00 offset, and 10:00 UTC naive:
    db.insert_stage_event(application_id=app["id"], stage="phone_screen",
                          event_type="email", event_date="2026-05-01T13:00:00+02:00",
                          subject="later")
    db.insert_stage_event(application_id=app["id"], stage="applied",
                          event_type="email", event_date="2026-05-01T10:00:00",
                          subject="earlier")
    events = db.get_events(app["id"])
    assert [e["subject"] for e in events] == ["earlier", "later"]


def test_flag_ghosted_with_legacy_format(db):
    """An application stuck in 'applied' whose last_updated was written by
    SQLite CURRENT_TIMESTAMP must still be flagged after the cutoff."""
    old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO applications (company, stage, last_updated) VALUES ('Old', 'applied', ?)",
            (old,),
        )
    db.init_db()  # normalize

    assert db.flag_ghosted(30) == 1
    assert db.find_application_by_company("Old")["stage"] == "ghosted"


def test_flag_ghosted_spares_recent(db):
    db.create_application(company="Fresh", role=None, stage="applied",
                          applied_date=None, source="manual", notes=None, job_url=None)
    assert db.flag_ghosted(30) == 0
    assert db.find_application_by_company("Fresh")["stage"] == "applied"
