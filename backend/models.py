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


class ApplicationUpdate(BaseModel):
    company: Optional[str] = None
    role: Optional[str] = None
    stage: Optional[str] = None
    applied_date: Optional[date] = None
    source: Optional[str] = None
    notes: Optional[str] = None
    job_url: Optional[str] = None


class MoveStageRequest(BaseModel):
    stage: str
    notes: Optional[str] = None
    event_date: Optional[datetime] = None


class SettingsUpdate(BaseModel):
    gmail_label: Optional[str] = None
    ghosted_days: Optional[int] = None
    min_confidence: Optional[float] = None

    @field_validator("ghosted_days")
    @classmethod
    def _check_ghosted_days(cls, v):
        # A non-positive value would back-date the cutoff to now-or-future and
        # flag every 'applied' application as ghosted on the next sync.
        if v is not None and v < 1:
            raise ValueError("ghosted_days must be at least 1")
        return v

    @field_validator("min_confidence")
    @classmethod
    def _check_min_confidence(cls, v):
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("min_confidence must be between 0.0 and 1.0")
        return v


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
