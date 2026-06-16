import json
import os
import re
import anthropic
from typing import Optional
from dotenv import load_dotenv
from backend.models import ClassificationResult

load_dotenv(override=True)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


# The static instructions live in the system block (with a cache breakpoint)
# so the per-email user message carries only the email itself. Note: at the
# current prompt size this is below Haiku 4.5's minimum cacheable prefix
# (4096 tokens), so the breakpoint is inert today — it engages automatically
# if the instructions ever grow past the minimum, and costs nothing until then.
_EMAIL_SYSTEM = """\
You classify emails to determine if they are related to a job application process. \
Respond with JSON only — no markdown, no extra text.

You must read the full body — the subject line is often misleading.

IMPORTANT: Subject lines frequently contradict the email's actual meaning:
- A subject like "Opportunity at X" or "Role at X" can contain a rejection in the body.
- A subject like "Thank you for applying" can be a confirmation OR a rejection.
- Always let the body content determine the stage. The body is the ground truth.

Return JSON:
{
  "company": "Company Name or null",
  "role": "Job Title or null",
  "stage": "applied|phone_screen|interview|technical_assessment|hr_interview|offer|rejected|other",
  "confidence": 0.0-1.0,
  "is_job_related": true or false,
  "is_referral": true or false
}

Stage definitions (use body content to decide, not the subject):
- applied: application acknowledgment/confirmation, referral notification (e.g. "you've been recommended for a role at X"), OR a recruiter cold-contacting you to pitch an open role. Any initial contact — whether self-applied, referred, or recruiter outreach — is "applied".
- phone_screen: initial HR/recruiter phone or video call is being scheduled.
- interview: first-round interview is being scheduled (after phone screen).
- technical_assessment: home assignment or in-person technical test, comes AFTER first interview.
- hr_interview: final-round culture/HR interview being scheduled.
- offer: job offer extended to the candidate.
- rejected: body communicates that the candidacy is not moving forward, regardless of what the subject says. Look for phrases like "not moving forward", "decided to pursue other candidates", "unfortunately", "we won't be", "not a fit", etc.
- other: job-related but doesn't fit any stage above.

Key distinctions:
- "You've been recommended / referred to [Company]" → applied (is_referral: true)
- "We found your profile and think you'd be a great fit" (unprompted cold outreach) → applied (is_referral: false)
- Subject says "opportunity" but body says "unfortunately we won't be moving forward" → rejected"""

_EMAIL_PROMPT = """\
From: {sender}
Subject: {subject}
Body excerpt:
{body}"""

_CALENDAR_SYSTEM = """\
You classify calendar events to determine if they are job interviews or \
hiring-related meetings. Respond with JSON only — no markdown, no extra text.

Return JSON:
{
  "company": "Company Name or null",
  "role": "Job Title or null",
  "stage": "phone_screen|interview|technical_assessment|hr_interview|other",
  "confidence": 0.0-1.0,
  "is_job_related": true or false
}

Stage definitions:
- phone_screen: initial recruiter/HR call
- interview: first-round interview
- technical_assessment: technical/coding interview or take-home debrief (after first interview)
- hr_interview: final-round HR/culture interview
- other: job-related but doesn't fit"""

_CALENDAR_PROMPT = """\
Title: {title}
Description: {description}"""


# ATS notification domains where the company name is the subdomain
_ATS_DOMAINS = {
    "comeet-notifications.com",
    "greenhouse.io",
    "lever.co",
    "jobvite.com",
    "icims.com",
    "workday.com",
    "myworkdayjobs.com",
    "successfactors.com",
    "smartrecruiters.com",
    "taleo.net",
    "brassring.com",
    "bamboohr.com",
    "ashbyhq.com",
    "rippling.com",
    "dover.com",
}


def _company_from_sender(sender: str) -> Optional[str]:
    """Extract company name from ATS notification sender domains."""
    match = re.search(r'[\w.+-]+@([\w.-]+)', sender)
    if not match:
        return None
    domain = match.group(1).lower()
    parts = domain.split(".")
    # Need at least subdomain.ats-domain.tld
    if len(parts) < 3:
        return None
    # Check if the base domain (last two parts) is a known ATS
    base = ".".join(parts[-2:])
    # Some ATS use three-part base domains like myworkdayjobs.com
    base3 = ".".join(parts[-3:]) if len(parts) >= 3 else base
    if base in _ATS_DOMAINS or base3 in _ATS_DOMAINS:
        subdomain = parts[0]
        # Skip generic subdomains that aren't company names
        if subdomain in ("notifications", "noreply", "no-reply", "mail", "email",
                         "jobs", "recruiting", "careers", "hr", "talent"):
            return None
        return subdomain.replace("-", " ").replace("_", " ").title()
    return None


_REJECTION_PHRASES = [
    "not moving forward",
    "decided not to move forward",
    "will not be moving forward",
    "won't be moving forward",
    "not be moving forward",
    "decided to pursue other candidates",
    "pursuing other candidates",
    "chosen to move forward with other",
    "selected other candidates",
    "position has been filled",
    "we have filled",
    "not a fit",
    "not the right fit",
    "not the best fit",
    "unfortunately",
    "we regret",
    "regret to inform",
    "after careful consideration",
    "we've decided",
    "we have decided",
    "will not be proceeding",
    "not proceeding",
    "moving in a different direction",
    "decided to go in a different direction",
]


def classify_email(sender: str, subject: str, body: str) -> ClassificationResult:
    prompt = _EMAIL_PROMPT.format(
        sender=sender,
        subject=subject,
        body=body[:1500],
    )
    result = _classify(prompt, _EMAIL_SYSTEM)
    # Hard override: if the body contains unambiguous rejection language, trust it
    # over the AI stage — Haiku can be fooled by subject lines like "opportunity at X"
    body_lower = body.lower()
    if result.is_job_related and any(p in body_lower for p in _REJECTION_PHRASES):
        result = ClassificationResult(
            company=result.company,
            role=result.role,
            stage="rejected",
            confidence=max(result.confidence, 0.9),
            is_job_related=True,
            is_referral=result.is_referral,
        )
    # Fallback: extract company from ATS sender domain when AI returned null
    if not result.company:
        result = ClassificationResult(
            company=_company_from_sender(sender),
            role=result.role,
            stage=result.stage,
            confidence=result.confidence,
            is_job_related=result.is_job_related,
            is_referral=result.is_referral,
        )
    return result


def classify_calendar_event(title: str, description: str) -> ClassificationResult:
    prompt = _CALENDAR_PROMPT.format(
        title=title,
        description=(description or "")[:400],
    )
    return _classify(prompt, _CALENDAR_SYSTEM)


def _classify(prompt: str, system: str) -> ClassificationResult:
    client = _get_client()
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=[{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ClassificationResult(
            company=None, role=None, stage="other",
            confidence=0.0, is_job_related=False,
        )
    return ClassificationResult(
        company=data.get("company"),
        role=data.get("role"),
        stage=data.get("stage", "other"),
        confidence=float(data.get("confidence", 0.0)),
        is_job_related=bool(data.get("is_job_related", False)),
        is_referral=bool(data.get("is_referral", False)),
    )
