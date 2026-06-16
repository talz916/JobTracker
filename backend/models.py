import re
from typing import Optional, List
from pydantic import BaseModel, field_validator
from datetime import date, datetime

STAGE_RANK = {
    "applied": 1,
    "phone_screen": 2,
    "interview": 3,
    "technical_assessment": 4,
    "hr_interview": 5,
    "offer": 6,
    "rejected": None,
    "declined_offer": None,
    "ghosted": None,
}

TERMINAL_STAGES = {"rejected", "declined_offer", "ghosted"}

ALL_STAGES = list(STAGE_RANK.keys())

ALLOWED_SOURCES = {"manual", "email", "calendar", "referral", "auto"}


def _validate_stage(v: Optional[str]) -> Optional[str]:
    if v is not None and v not in ALL_STAGES:
        raise ValueError(f"invalid stage {v!r}; valid stages: {ALL_STAGES}")
    return v


def _validate_source(v: Optional[str]) -> Optional[str]:
    if v is not None and v not in ALLOWED_SOURCES:
        raise ValueError(f"invalid source {v!r}; valid sources: {sorted(ALLOWED_SOURCES)}")
    return v


def _validate_job_url(v: Optional[str]) -> Optional[str]:
    if v is not None and v != "" and not re.match(r"^https?://", v, re.IGNORECASE):
        raise ValueError("job_url must be an http(s) URL")
    return v


def can_advance(current: str, new: str) -> bool:
    if new in TERMINAL_STAGES:
        return True
    current_rank = STAGE_RANK.get(current)
    new_rank = STAGE_RANK.get(new)
    if current_rank is None or new_rank is None:
        return False
    return new_rank > current_rank


class ApplicationCreate(BaseModel):
    company: str
    role: Optional[str] = None
    stage: str = "applied"
    applied_date: Optional[date] = None
    source: str = "manual"
    notes: Optional[str] = None
    job_url: Optional[str] = None

    _check_stage = field_validator("stage")(_validate_stage)
    _check_source = field_validator("source")(_validate_source)
    _check_job_url = field_validator("job_url")(_validate_job_url)


class ApplicationUpdate(BaseModel):
    company: Optional[str] = None
    role: Optional[str] = None
    stage: Optional[str] = None
    applied_date: Optional[date] = None
    source: Optional[str] = None
    notes: Optional[str] = None
    job_url: Optional[str] = None

    _check_stage = field_validator("stage")(_validate_stage)
    _check_source = field_validator("source")(_validate_source)
    _check_job_url = field_validator("job_url")(_validate_job_url)


class MoveStageRequest(BaseModel):
    stage: str
    notes: Optional[str] = None
    event_date: Optional[datetime] = None

    _check_stage = field_validator("stage")(_validate_stage)


class SettingsUpdate(BaseModel):
    gmail_label: Optional[str] = None
    ghosted_days: Optional[int] = None
    min_confidence: Optional[float] = None


class MergeRequest(BaseModel):
    keep_id: int
    merge_ids: List[int]


class ClassificationResult(BaseModel):
    company: Optional[str]
    role: Optional[str]
    stage: str
    confidence: float
    is_job_related: bool
    is_referral: bool = False
