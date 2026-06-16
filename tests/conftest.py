import sys
from pathlib import Path

import pytest

# Make the repo root importable regardless of where pytest is invoked from
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """A fresh, isolated database for each test. Returns the database module
    with DB_PATH pointed at a throwaway file."""
    import backend.database as db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return db
