"""Unit tests for the stage-transition rules and the settings guardrails."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError

from backend.models import can_advance, SettingsUpdate, ALL_STAGES, TERMINAL_STAGES


class TestCanAdvance:
    def test_forward_moves_allowed(self):
        assert can_advance("applied", "phone_screen")
        assert can_advance("phone_screen", "offer")

    def test_same_or_backward_rejected(self):
        assert not can_advance("interview", "interview")
        assert not can_advance("offer", "applied")

    def test_any_active_can_go_terminal(self):
        for stage in ("applied", "phone_screen", "interview", "offer"):
            assert can_advance(stage, "rejected")
            assert can_advance(stage, "ghosted")

    def test_from_terminal_is_rejected(self):
        # A rejected application can't "advance" to an active stage
        assert not can_advance("rejected", "interview")

    def test_unknown_stage_rejected(self):
        assert not can_advance("applied", "banana")
        assert not can_advance("banana", "offer")


class TestSettingsGuardrails:
    def test_valid_settings_accepted(self):
        s = SettingsUpdate(ghosted_days=30, min_confidence=0.6)
        assert s.ghosted_days == 30 and s.min_confidence == 0.6

    @pytest.mark.parametrize("bad", [0, -1, -30])
    def test_non_positive_ghosted_days_rejected(self, bad):
        with pytest.raises(ValidationError):
            SettingsUpdate(ghosted_days=bad)

    @pytest.mark.parametrize("bad", [-0.1, 1.1, 5.0])
    def test_out_of_range_confidence_rejected(self, bad):
        with pytest.raises(ValidationError):
            SettingsUpdate(min_confidence=bad)

    @pytest.mark.parametrize("ok", [0.0, 0.5, 1.0])
    def test_boundary_confidence_accepted(self, ok):
        assert SettingsUpdate(min_confidence=ok).min_confidence == ok

    def test_partial_update_allows_none(self):
        # Only updating the label leaves the numeric fields unset
        s = SettingsUpdate(gmail_label="Jobs")
        assert s.ghosted_days is None and s.min_confidence is None


def test_terminal_stages_have_no_rank():
    from backend.models import STAGE_RANK
    for s in TERMINAL_STAGES:
        assert STAGE_RANK[s] is None
    assert set(TERMINAL_STAGES) <= set(ALL_STAGES)
