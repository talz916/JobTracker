"""Tests for the parallelized email sync: threads are fetched and classified
concurrently, DB mutations are serialized, and the per-thread dedup lookup is
replaced by one bulk query."""

import base64
import sys
import threading
import time
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
    # The placeholder cache is module-global; reset it per test
    monkeypatch.setattr(email_sync, "_placeholder_id", None)
    return database


def make_msg(msg_id, sender, subject, body):
    return {
        "id": msg_id,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Mon, 1 Jun 2026 10:00:00 +0000"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


class FakeGmail:
    """Supports threads().list / threads().get / messages().get."""

    def __init__(self, threads_by_id):
        self.threads_by_id = threads_by_id

    def users(self):
        return self

    def threads(self):
        return self

    def messages(self):
        return self

    def list(self, userId=None, q=None, maxResults=None, pageToken=None):
        class _Call:
            def __init__(self, data):
                self._data = data

            def execute(self):
                return self._data

        # Only return results for the first query so threads aren't repeated
        if "application received" in (q or ""):
            return _Call({"threads": [{"id": tid} for tid in self.threads_by_id]})
        return _Call({"threads": []})

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


def test_parallel_sync_no_duplicate_applications(db, monkeypatch):
    """Ten emails from the same company processed by parallel workers must
    still produce exactly one application."""
    threads = {
        f"t{i}": [make_msg(f"m{i}", "jobs@acme.com", f"Update {i}",
                           "We received your application.")]
        for i in range(10)
    }

    in_flight = {"now": 0, "max": 0}
    gauge = threading.Lock()

    def slow_classify(sender, subject, body):
        with gauge:
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
        time.sleep(0.05)  # simulate API latency so workers overlap
        with gauge:
            in_flight["now"] -= 1
        return ClassificationResult(company="Acme", role=None, stage="applied",
                                    confidence=0.95, is_job_related=True)

    monkeypatch.setattr(email_sync, "classify_email", slow_classify)

    result = email_sync.sync_emails(service=FakeGmail(threads), max_workers=6)

    assert result["threads_checked"] == 10
    apps = [a for a in db.list_applications()
            if a["company"] != "__skipped__"]
    assert len(apps) == 1  # no duplicates despite concurrency
    assert len(db.get_events(apps[0]["id"])) == 10  # every email recorded
    assert in_flight["max"] > 1  # classification actually ran concurrently


def test_sync_skips_known_threads_without_classifying(db, monkeypatch):
    threads = {"t1": [make_msg("m1", "jobs@acme.com", "Thanks",
                               "We received your application.")]}
    calls = []

    def stub(sender, subject, body):
        calls.append(body)
        return ClassificationResult(company="Acme", role=None, stage="applied",
                                    confidence=0.95, is_job_related=True)

    monkeypatch.setattr(email_sync, "classify_email", stub)

    email_sync.sync_emails(service=FakeGmail(threads))
    assert len(calls) == 1

    email_sync.sync_emails(service=FakeGmail(threads))  # second run: all cached
    assert len(calls) == 1


def test_all_thread_message_ids_bulk_lookup(db):
    app = db.create_application(company="Acme", role=None, stage="applied",
                                applied_date=None, source="email", notes=None,
                                job_url=None)
    db.insert_stage_event(application_id=app["id"], stage="applied",
                          event_type="email", thread_id="t1", message_id="m1")
    db.insert_stage_event(application_id=app["id"], stage="rejected",
                          event_type="email", thread_id="t1", message_id="m2")
    db.insert_stage_event(application_id=app["id"], stage="applied",
                          event_type="manual")  # no thread — excluded

    known = db.all_thread_message_ids()
    assert known == {"t1": {"m1", "m2"}}


def test_wal_mode_enabled(db):
    with db.get_db() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_each_worker_builds_its_own_service(db, monkeypatch):
    """Gmail's httplib2 transport is not thread-safe — sharing one service
    across workers corrupts the TLS stream ('[SSL: WRONG_VERSION_NUMBER]').
    When no service is injected, sync must build a client per worker thread,
    not reuse a single shared one."""
    threads = {
        f"t{i}": [make_msg(f"m{i}", "jobs@acme.com", f"S{i}",
                           "We received your application.")]
        for i in range(8)
    }
    built = []

    def fake_get_service():
        svc = FakeGmail(threads)
        built.append(svc)
        return svc

    monkeypatch.setattr(email_sync, "_get_service", fake_get_service)
    monkeypatch.setattr(email_sync, "classify_email",
                        lambda s, su, b: ClassificationResult(
                            company="Acme", role=None, stage="applied",
                            confidence=0.95, is_job_related=True))

    # No service injected → production path must build per-thread clients.
    email_sync.sync_emails(max_workers=4)

    # At least the collection client plus one per-worker client — never just one.
    assert len(built) >= 2
