"""The OAuth callback used to 422 when the user cancelled the Google consent
screen (no ?code param), 500 with a raw traceback when token exchange failed,
and serve the dashboard at the callback URL — leaving the one-time auth code
in the browser's address bar and history. All paths now redirect cleanly."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))

from fastapi.testclient import TestClient

import backend.main as main

client = TestClient(main.app, follow_redirects=False)


@pytest.fixture(autouse=True)
def no_pending_flow():
    main._pending_flow = None
    yield
    main._pending_flow = None


def test_consent_denied_redirects_home():
    res = client.get("/api/auth/callback?error=access_denied")
    assert res.status_code == 303
    assert res.headers["location"] == "/"


def test_missing_code_redirects_home():
    res = client.get("/api/auth/callback")
    assert res.status_code == 303
    assert res.headers["location"] == "/"


def test_code_without_pending_flow_redirects_home():
    res = client.get("/api/auth/callback?code=abc&state=xyz")
    assert res.status_code == 303
    assert res.headers["location"] == "/"


def test_token_exchange_failure_redirects_with_flag(tmp_path, monkeypatch):
    class BoomFlow:
        def fetch_token(self, **kwargs):
            raise RuntimeError("invalid_grant")

    main._pending_flow = BoomFlow()
    # Make sure a failure can never write a token file
    monkeypatch.setattr(main, "TOKEN_FILE", str(tmp_path / "token.json"))

    res = client.get("/api/auth/callback?code=abc&state=xyz")
    assert res.status_code == 303
    assert res.headers["location"] == "/?auth_error=1"
    assert not (tmp_path / "token.json").exists()
    assert main._pending_flow is None  # consumed, not reusable


def test_successful_exchange_writes_token_and_redirects(tmp_path, monkeypatch):
    class GoodCreds:
        def to_json(self):
            return '{"token": "t"}'

    class GoodFlow:
        credentials = GoodCreds()

        def fetch_token(self, **kwargs):
            pass

    main._pending_flow = GoodFlow()
    token_file = tmp_path / "credentials" / "token.json"
    monkeypatch.setattr(main, "TOKEN_FILE", str(token_file))

    res = client.get("/api/auth/callback?code=abc&state=xyz")
    assert res.status_code == 303
    assert res.headers["location"] == "/"
    assert token_file.read_text() == '{"token": "t"}'
