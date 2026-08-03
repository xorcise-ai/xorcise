from __future__ import annotations

import pytest

from xorcise.core.contracts.grading import CheckVerdict


@pytest.mark.unit
def test_check_verdict_carries_resolved_value():
    v = CheckVerdict(
        id="flag-correct",
        source="artifacts",
        ref="flag",
        op="equals",
        value="XORCISE{x}",
        passed=True,
        weight=0.7,
    )
    assert v.passed is True and v.value == "XORCISE{x}" and v.weight == 0.7
    assert v.error is None  # additive field defaults None — wire shape preserved


@pytest.mark.unit
def test_check_verdict_carries_execution_error():
    """Defensive grading: an unexecutable check discloses WHY it failed (never raises)."""
    v = CheckVerdict(
        id="legacy",
        source="artifacts",
        ref="flag",
        op="regex",
        error="unknown op 'regex' (known: equals, lesser_than, matches_format, observed)",
    )
    assert v.passed is False and v.value is None
    assert v.error is not None and "unknown op 'regex'" in v.error
