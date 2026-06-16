"""Tests for the classifier's response parsing, rejection override, and ATS
sender fallback. The Anthropic call is stubbed — no network, no API key."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.classifier as classifier
from backend.classifier import _company_from_sender


def _stub_anthropic(monkeypatch, raw_text):
    """Make _classify return a model response of `raw_text` without a network call."""
    def fake_create(**kwargs):
        return SimpleNamespace(content=[SimpleNamespace(text=raw_text)])

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    monkeypatch.setattr(classifier, "_get_client", lambda: fake_client)


def test_parses_plain_json(monkeypatch):
    _stub_anthropic(monkeypatch, '{"company": "Acme", "role": "PM", "stage": "applied", '
                                 '"confidence": 0.9, "is_job_related": true}')
    r = classifier.classify_email("jobs@acme.com", "Thanks", "We received your application.")
    assert r.company == "Acme" and r.stage == "applied" and r.is_job_related


def test_parses_markdown_fenced_json(monkeypatch):
    _stub_anthropic(monkeypatch, '```json\n{"company": "Acme", "role": null, '
                                 '"stage": "interview", "confidence": 0.8, '
                                 '"is_job_related": true}\n```')
    r = classifier.classify_email("jobs@acme.com", "Interview", "Let's schedule.")
    assert r.stage == "interview" and r.confidence == 0.8


def test_malformed_json_returns_safe_default(monkeypatch):
    _stub_anthropic(monkeypatch, "Sorry, I cannot help with that.")
    r = classifier.classify_email("x@y.com", "Subject", "Body")
    assert r.is_job_related is False and r.stage == "other" and r.confidence == 0.0


def test_rejection_phrase_overrides_ai_stage(monkeypatch):
    # AI is fooled by the upbeat subject and says "applied"; the body is a rejection
    _stub_anthropic(monkeypatch, '{"company": "Acme", "role": null, "stage": "applied", '
                                 '"confidence": 0.7, "is_job_related": true}')
    r = classifier.classify_email(
        "jobs@acme.com", "Opportunity at Acme",
        "After careful consideration, unfortunately we will not be moving forward.",
    )
    assert r.stage == "rejected"
    assert r.confidence >= 0.9  # override bumps confidence


def test_rejection_override_skipped_for_non_job_email(monkeypatch):
    # 'unfortunately' appears but the email isn't job-related — don't mislabel
    _stub_anthropic(monkeypatch, '{"company": null, "role": null, "stage": "other", '
                                 '"confidence": 0.2, "is_job_related": false}')
    r = classifier.classify_email("news@x.com", "Newsletter",
                                  "Unfortunately the sale has ended.")
    assert r.stage == "other" and r.is_job_related is False


def test_ats_sender_fallback_when_company_null(monkeypatch):
    _stub_anthropic(monkeypatch, '{"company": null, "role": "Engineer", "stage": "applied", '
                                 '"confidence": 0.8, "is_job_related": true}')
    r = classifier.classify_email(
        "notify@stripe.greenhouse.io", "Application received", "Thanks for applying.")
    assert r.company == "Stripe"  # pulled from the ATS subdomain


class TestCompanyFromSender:
    def test_extracts_ats_subdomain(self):
        assert _company_from_sender("jobs@acme.greenhouse.io") == "Acme"
        assert _company_from_sender("x@big-corp.lever.co") == "Big Corp"

    def test_three_part_ats_base(self):
        assert _company_from_sender("x@acme.myworkdayjobs.com") == "Acme"

    def test_generic_subdomain_skipped(self):
        assert _company_from_sender("noreply@notifications.greenhouse.io") is None
        assert _company_from_sender("x@jobs.lever.co") is None

    def test_non_ats_domain_returns_none(self):
        assert _company_from_sender("ceo@randomstartup.com") is None

    def test_unparseable_sender_returns_none(self):
        assert _company_from_sender("not an email") is None
