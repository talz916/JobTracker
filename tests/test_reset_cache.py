"""Regression tests for the reset-cache → sync crash.

Before the fix, both sync modules cached the '__skipped__' placeholder id in a
module global. reset_email_cache() deletes the placeholder once it has no
events left, so the cached id pointed at a dead row and the next sync died
with 'FOREIGN KEY constraint failed' (INSERT OR IGNORE does not suppress FK
violations)."""

import sqlite3

import pytest


def test_placeholder_created_and_reused(tmp_db):
    pid = tmp_db.get_or_create_placeholder()
    assert tmp_db.get_application(pid)["company"] == tmp_db.PLACEHOLDER_COMPANY
    assert tmp_db.get_or_create_placeholder() == pid


def test_stale_placeholder_id_raises_fk_error(tmp_db):
    """Documents the failure mode the fix prevents: inserting against a
    placeholder id that reset_email_cache deleted blows up."""
    stale_id = tmp_db.get_or_create_placeholder()
    tmp_db.insert_stage_event(
        application_id=stale_id, stage="other", event_type="email", thread_id="t1"
    )
    tmp_db.reset_email_cache()  # deletes the now-eventless placeholder
    assert tmp_db.get_application(stale_id) is None

    with pytest.raises(sqlite3.IntegrityError):
        tmp_db.insert_stage_event(
            application_id=stale_id, stage="other", event_type="email", thread_id="t2"
        )


def test_sync_survives_reset_cache(tmp_db):
    """The full sequence that used to crash: cache a skipped thread, reset the
    email cache, then file another skipped thread."""
    first = tmp_db.get_or_create_placeholder()
    tmp_db.insert_stage_event(
        application_id=first, stage="other", event_type="email", thread_id="t1"
    )

    result = tmp_db.reset_email_cache()
    assert result["events_cleared"] == 1
    assert result["applications_removed"] == 1

    fresh = tmp_db.get_or_create_placeholder()  # what the sync now calls each time
    assert fresh != first
    tmp_db.insert_stage_event(
        application_id=fresh, stage="other", event_type="email", thread_id="t2"
    )
    assert tmp_db.thread_exists("t2")


def test_reset_cache_keeps_manual_applications(tmp_db):
    app = tmp_db.create_application(
        company="Acme", role="PM", stage="applied",
        applied_date=None, source="manual", notes=None, job_url=None,
    )
    tmp_db.reset_email_cache()
    assert tmp_db.get_application(app["id"]) is not None
