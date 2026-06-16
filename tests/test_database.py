"""Tests for database semantics: merge, ghosted-flagging boundaries, the email
body decoder, and event-driven stage recomputation."""

import base64
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.email_sync import _decode_body


def _mk(db, company, stage="applied", source="manual"):
    return db.create_application(company=company, role=None, stage=stage,
                                 applied_date=None, source=source, notes=None,
                                 job_url=None)


class TestMerge:
    def test_keeps_most_advanced_stage(self, tmp_db):
        a = _mk(tmp_db, "Acme", "applied")
        b = _mk(tmp_db, "Acme dup", "interview")
        result = tmp_db.merge_applications(a["id"], [b["id"]])
        assert result["stage"] == "interview"
        assert tmp_db.get_application(b["id"]) is None  # duplicate deleted

    def test_terminal_stage_wins_over_active(self, tmp_db):
        a = _mk(tmp_db, "Acme", "interview")
        b = _mk(tmp_db, "Acme dup", "rejected")
        result = tmp_db.merge_applications(a["id"], [b["id"]])
        assert result["stage"] == "rejected"

    def test_events_move_to_kept_record(self, tmp_db):
        a = _mk(tmp_db, "Acme", "applied")
        b = _mk(tmp_db, "Acme dup", "phone_screen")
        tmp_db.insert_stage_event(application_id=b["id"], stage="phone_screen",
                                  event_type="manual", thread_id="t-b")
        tmp_db.merge_applications(a["id"], [b["id"]])
        kept_events = tmp_db.get_events(a["id"])
        # the moved event plus the auto-logged "Merged duplicates" event
        assert any(e["thread_id"] == "t-b" for e in kept_events)
        assert any("Merged" in (e["subject"] or "") for e in kept_events)

    def test_single_id_is_noop(self, tmp_db):
        a = _mk(tmp_db, "Acme")
        assert tmp_db.merge_applications(a["id"], [99999]) is None


class TestFlagGhosted:
    def _backdate(self, db, app_id, days):
        old = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with db.get_db() as conn:
            conn.execute("UPDATE applications SET last_updated = ? WHERE id = ?",
                         (old, app_id))

    def test_flags_stale_applied(self, tmp_db):
        a = _mk(tmp_db, "Stale", "applied")
        self._backdate(tmp_db, a["id"], 40)
        assert tmp_db.flag_ghosted(30) == 1
        assert tmp_db.get_application(a["id"])["stage"] == "ghosted"

    def test_spares_recent(self, tmp_db):
        a = _mk(tmp_db, "Fresh", "applied")
        self._backdate(tmp_db, a["id"], 10)
        assert tmp_db.flag_ghosted(30) == 0
        assert tmp_db.get_application(a["id"])["stage"] == "applied"

    def test_boundary_just_under_cutoff_spared(self, tmp_db):
        a = _mk(tmp_db, "Edge", "applied")
        self._backdate(tmp_db, a["id"], 29)
        assert tmp_db.flag_ghosted(30) == 0

    def test_only_applied_stage_flagged(self, tmp_db):
        a = _mk(tmp_db, "Interviewing", "interview")
        self._backdate(tmp_db, a["id"], 90)
        assert tmp_db.flag_ghosted(30) == 0  # interview stage is never auto-ghosted

    def test_logs_an_event(self, tmp_db):
        a = _mk(tmp_db, "Stale", "applied")
        self._backdate(tmp_db, a["id"], 40)
        tmp_db.flag_ghosted(30)
        events = tmp_db.get_events(a["id"])
        assert any(e["stage"] == "ghosted" and e["event_type"] == "auto" for e in events)


class TestDeleteEventRecompute:
    def test_stage_recomputed_after_event_delete(self, tmp_db):
        a = _mk(tmp_db, "Acme", "interview")
        tmp_db.insert_stage_event(application_id=a["id"], stage="applied",
                                  event_type="email", thread_id="t1")
        ev = tmp_db.insert_stage_event(application_id=a["id"], stage="interview",
                                       event_type="email", thread_id="t2")
        events = tmp_db.get_events(a["id"])
        interview_ev = next(e for e in events if e["stage"] == "interview")
        tmp_db.delete_event(interview_ev["id"])
        # only the 'applied' event remains → app falls back to applied
        assert tmp_db.get_application(a["id"])["stage"] == "applied"


class TestDecodeBody:
    def _b64(self, text):
        return base64.urlsafe_b64encode(text.encode()).decode()

    def test_plain_text(self):
        payload = {"mimeType": "text/plain", "body": {"data": self._b64("Hello world")}}
        assert _decode_body(payload) == "Hello world"

    def test_html_stripped_to_text(self):
        html = "<html><body><p>Hi <b>there</b></p></body></html>"
        payload = {"mimeType": "text/html", "body": {"data": self._b64(html)}}
        out = _decode_body(payload)
        assert "Hi" in out and "there" in out and "<b>" not in out

    def test_multipart_prefers_plain(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": self._b64("plain version")}},
                {"mimeType": "text/html", "body": {"data": self._b64("<p>html version</p>")}},
            ],
        }
        assert _decode_body(payload) == "plain version"

    def test_nested_multipart_recursion(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [{
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": self._b64("nested plain")}},
                ],
            }],
        }
        assert _decode_body(payload) == "nested plain"

    def test_empty_payload(self):
        assert _decode_body({"mimeType": "text/plain", "body": {}}) == ""
