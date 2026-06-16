"""Server-side validation: stage and source must come from the allowed lists,
and job_url must be a real http(s) link (a javascript: URL would execute when
rendered as a clickable link in the dashboard)."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))

from fastapi.testclient import TestClient

import backend.main as main

client = TestClient(main.app)


def _create(**overrides):
    body = {"company": "Acme", "role": "PM", "stage": "applied", "source": "manual"}
    body.update(overrides)
    return client.post("/api/applications", json=body)


def test_valid_application_created():
    res = _create(job_url="https://acme.com/jobs/123")
    assert res.status_code == 201
    assert res.json()["company"] == "Acme"


def test_invalid_stage_rejected():
    res = _create(stage="banana")
    assert res.status_code == 422
    assert "invalid stage" in res.text


def test_invalid_source_rejected():
    res = _create(source="<img src=x onerror=alert(1)>")
    assert res.status_code == 422
    assert "invalid source" in res.text


def test_javascript_url_rejected():
    res = _create(job_url="javascript:alert(document.cookie)")
    assert res.status_code == 422
    assert "http(s)" in res.text


def test_patch_validates_too():
    app_id = _create().json()["id"]
    assert client.patch(f"/api/applications/{app_id}",
                        json={"stage": "<script>"}).status_code == 422
    assert client.patch(f"/api/applications/{app_id}",
                        json={"source": "nonsense"}).status_code == 422
    assert client.patch(f"/api/applications/{app_id}",
                        json={"job_url": "ftp://x"}).status_code == 422

    ok = client.patch(f"/api/applications/{app_id}", json={"stage": "phone_screen"})
    assert ok.status_code == 200
    assert ok.json()["stage"] == "phone_screen"


def test_move_stage_validates():
    app_id = _create().json()["id"]
    res = client.post(f"/api/applications/{app_id}/move-stage",
                      json={"stage": "not-a-stage"})
    assert res.status_code == 422
