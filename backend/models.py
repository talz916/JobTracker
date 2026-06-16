from typing import Optional, List
from pydantic import BaseModel
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


def best_stage(stages):
    """The most-advanced stage in a list. Terminal stages (rejected/declined/
    ghosted) outrank everything; among the rest, the highest STAGE_RANK wins.
    Used to recompute an application's stage from its events."""
    def priority(s):
        return (1, 0) if s in TERMINAL_STAGES else (0, STAGE_RANK.get(s) or 0)
    return max(stages, key=priority)


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


class MergeRequest(BaseModel):
    keep_id: int
    merge_ids: List[int]


class ReassignEventRequest(BaseModel):
    application_id: int


class UpdateEventStageRequest(BaseModel):
    stage: str


class ClassificationResult(BaseModel):
    company: Optional[str]
    role: Optional[str]
    stage: str
    confidence: float
    is_job_related: bool
    is_referral: bool = False
