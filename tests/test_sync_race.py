"""The 'is a sync running?' check and the 'mark as running' write used to be
two separate lock acquisitions, so two concurrent requests could both start a
sync — doubling Gmail and Anthropic usage. _try_start_sync makes claiming the
slot atomic."""

import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Point the app at a throwaway DB before backend.main's import-time init_db()
os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))

import backend.main as main


@pytest.fixture(autouse=True)
def reset_sync_state():
    main._update_sync(running=False)
    yield
    main._update_sync(running=False)


def test_second_start_is_rejected():
    assert main._try_start_sync("emails") is True
    assert main._try_start_sync("emails") is False
    assert main._try_start_sync("calendar") is False

    main._update_sync(running=False)
    assert main._try_start_sync("calendar") is True


def test_start_resets_progress_fields():
    main._update_sync(running=False, processed=7, total=9, error="boom")
    assert main._try_start_sync("emails")
    state = main._get_sync_state()
    assert state["running"] is True
    assert state["type"] == "emails"
    assert state["processed"] == 0 and state["total"] == 0
    assert state["error"] is None and state["result"] is None


def test_only_one_winner_under_contention():
    """Many threads race for the slot at once; exactly one may win."""
    barrier = threading.Barrier(16)
    wins = []

    def contender():
        barrier.wait()
        if main._try_start_sync("emails"):
            wins.append(threading.get_ident())

    threads = [threading.Thread(target=contender) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(wins) == 1
