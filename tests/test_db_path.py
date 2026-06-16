"""The default DB path used to be CWD-relative ('jobhunter.db'), so starting
the server from another directory silently created a second, empty database.
The default is now anchored to the project root; DATABASE_PATH still wins."""

import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _reload_database(monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("DATABASE_PATH", raising=False)
    else:
        monkeypatch.setenv("DATABASE_PATH", env_value)
    import backend.database as database
    return importlib.reload(database)


def test_default_is_anchored_to_project_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # simulate launching from elsewhere
    database = _reload_database(monkeypatch, None)
    assert Path(database.DB_PATH).is_absolute()
    assert Path(database.DB_PATH) == PROJECT_ROOT / "jobhunter.db"


def test_env_override_still_wins(tmp_path, monkeypatch):
    custom = str(tmp_path / "custom.db")
    database = _reload_database(monkeypatch, custom)
    assert database.DB_PATH == custom
    # restore module state for other tests
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    importlib.reload(database)
