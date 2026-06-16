"""Tests for message-level thread tracking.

Previously only the first message of a Gmail thread was classified and the
thread was then locked forever by a unique index on thread_id — replies
(rejections, interview invites) were never seen. Now the newest unseen
message is classified whenever a thread grows."""

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.database as database
import backend.email_sync as email_sync
from backend.models import ClassificationResult


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    return database


def make_msg(msg_id, sender, subject, date, body):
    return {
        "id": msg_id,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


class FakeGmail:
    """The slice of the Gmail client used by _process_thread."""

    def __init__(self):
        self.threads_by_id = {}

    def users(self):
        return self

    def threads(self):
        return self

    def messages(self):
        return self

    def get(self, userId=None, id=None, format=None, metadataHeaders=None):
        class _Call:
            def __init__(self, data):
                self._data = data

            def execute(self):
                return self._data

        if format == "metadata":
            msgs = self.threads_by_id[id]
            meta = [{"id": m["id"], "payload": {"headers": m["payload"]["headers"]}}
                    for m in msgs]
            return _Call({"id": id, "messages": meta})
        for msgs in self.threads_by_id.values():
            for m in msgs:
                if m["id"] == id:
                    return _Call(m)
        raise AssertionError(f"unknown message id {id}")


@pytest.fixture
def classify_stub(monkeypatch):
    """Stage decided by body content; records every call."""
    calls = []

    def stub(sender, subject, body):
        calls.append(body)
        stage = "rejected" if "unfortunately" in body.lower() else "applied"
        return ClassificationResult(
            company="Acme", role=None, stage=stage,
            confidence=0.95, is_job_related=True,
        )

    monkeypatch.setattr(email_sync, "classify_email", stub)
    return calls


def test_new_thread_classified_once(db, classify_stub):
    gmail = FakeGmail()
    gmail.threads_by_id["t1"] = [
        make_msg("m1", "jobs@acme.com", "Thanks for applying",
                 "Mon, 1 Jun 2026 10:00:00 +0000", "We received your application."),
    ]

    result = email_sync._process_thread(gmail, "t1", 0.6)
    assert result["company"] == "Acme"
    assert len(classify_stub) == 1

    app = db.find_application_by_company("Acme")
    assert app["stage"] == "applied"
    events = db.get_events(app["id"])
    assert [e["message_id"] for e in events] == ["m1"]

    # Re-running the sync must not re-classify or duplicate anything
    assert email_sync._process_thread(gmail, "t1", 0.6) is None
    assert len(classify_stub) == 1
    assert len(db.get_events(app["id"])) == 1


def test_reply_in_thread_updates_stage(db, classify_stub):
    gmail = FakeGmail()
    gmail.threads_by_id["t1"] = [
        make_msg("m1", "jobs@acme.com", "Thanks for applying",
                 "Mon, 1 Jun 2026 10:00:00 +0000", "We received your application."),
    ]
    email_sync._process_thread(gmail, "t1", 0.6)

    # The rejection arrives as a reply in the same thread
    gmail.threads_by_id["t1"].append(
        make_msg("m2", "jobs@acme.com", "Re: Thanks for applying",
                 "Mon, 8 Jun 2026 09:00:00 +0000",
                 "Unfortunately we will not be moving forward."),
    )
    result = email_sync._process_thread(gmail, "t1", 0.6)
    assert result["stage"] == "rejected"

    # Only the new message was classified
    assert len(classify_stub) == 2
    assert "Unfortunately" in classify_stub[-1]

    app = db.find_application_by_company("Acme")
    assert app["stage"] == "rejected"
    assert db.thread_message_ids("t1") == {"m1", "m2"}


def test_legacy_row_single_message_backfilled_not_reclassified(db, classify_stub):
    """A thread recorded before the migration (message_id NULL) with no new
    messages must be stamped, not re-classified."""
    app = db.create_application(company="Acme", role=None, stage="applied",
                                applied_date=None, source="email", notes=None,
                                job_url=None)
    db.insert_stage_event(application_id=app["id"], stage="applied",
                          event_type="email", thread_id="t1")  # legacy: no message_id

    gmail = FakeGmail()
    gmail.threads_by_id["t1"] = [
        make_msg("m1", "jobs@acme.com", "Thanks for applying",
                 "Mon, 1 Jun 2026 10:00:00 +0000", "We received your application."),
    ]

    assert email_sync._process_thread(gmail, "t1", 0.6) is None
    assert len(classify_stub) == 0
    assert db.thread_message_ids("t1") == {"m1"}


def test_legacy_row_with_unseen_reply_classified(db, classify_stub):
    """A pre-migration thread that has since received a reply: the legacy row
    is stamped with the first message id and the reply is classified."""
    app = db.create_application(company="Acme", role=None, stage="applied",
                                applied_date=None, source="email", notes=None,
                                job_url=None)
    db.insert_stage_event(application_id=app["id"], stage="applied",
                          event_type="email", thread_id="t1")

    gmail = FakeGmail()
    gmail.threads_by_id["t1"] = [
        make_msg("m1", "jobs@acme.com", "Thanks for applying",
                 "Mon, 1 Jun 2026 10:00:00 +0000", "We received your application."),
        make_msg("m2", "jobs@acme.com", "Re: Thanks for applying",
                 "Mon, 8 Jun 2026 09:00:00 +0000",
                 "Unfortunately we will not be moving forward."),
    ]

    result = email_sync._process_thread(gmail, "t1", 0.6)
    assert result["stage"] == "rejected"
    assert len(classify_stub) == 1
    assert db.thread_message_ids("t1") == {"m1", "m2"}
    assert db.get_application(app["id"])["stage"] == "rejected"


def test_migration_drops_old_unique_thread_index(db):
    """Two events for the same thread (different messages) must both persist."""
    app = db.create_application(company="Acme", role=None, stage="applied",
                                applied_date=None, source="email", notes=None,
                                job_url=None)
    db.insert_stage_event(application_id=app["id"], stage="applied",
                          event_type="email", thread_id="t1", message_id="m1")
    db.insert_stage_event(application_id=app["id"], stage="rejected",
                          event_type="email", thread_id="t1", message_id="m2")
    assert len(db.get_events(app["id"])) == 2

    # Same (thread, message) twice is still deduplicated
    db.insert_stage_event(application_id=app["id"], stage="rejected",
                          event_type="email", thread_id="t1", message_id="m2")
    assert len(db.get_events(app["id"])) == 2


def test_migration_on_legacy_schema(tmp_path, monkeypatch):
    """init_db on a database created with the old schema (unique thread_id
    index) drops the old index and allows per-message events."""
    import sqlite3

    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.executescript("""
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL, role TEXT,
            stage TEXT NOT NULL DEFAULT 'applied',
            applied_date DATE, last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
            source TEXT DEFAULT 'email', notes TEXT, job_url TEXT
        );
        CREATE TABLE stage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            stage TEXT NOT NULL, event_type TEXT NOT NULL,
            event_date DATETIME, subject TEXT, thread_id TEXT,
            calendar_event_id TEXT, snippet TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE UNIQUE INDEX idx_thread
            ON stage_events(thread_id) WHERE thread_id IS NOT NULL;
        INSERT INTO applications (company, stage) VALUES ('Acme', 'applied');
        INSERT INTO stage_events (application_id, stage, event_type, thread_id)
            VALUES (1, 'applied', 'email', 't1');
    """)
    conn.commit()
    conn.close()

    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    database.init_db()

    db = database
    db.insert_stage_event(application_id=1, stage="rejected",
                          event_type="email", thread_id="t1", message_id="m2")
    assert len(db.get_events(1)) == 2
    assert db.thread_message_ids("t1") == {None, "m2"}
