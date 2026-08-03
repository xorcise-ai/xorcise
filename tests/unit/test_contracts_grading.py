from __future__ import annotations

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.grading import GradeRequest, GradeResult, ScoreBreakdown


def test_grade_request_minimal() -> None:
    req = GradeRequest(run_id="r1", trace_ref="r1")
    assert req.artifacts == ()
    assert req.rubric_ref is None


def test_grade_result_carries_breakdown() -> None:
    result = GradeResult(
        run_id="r1", overall=0.5, breakdown=ScoreBreakdown(deterministic=0.5, judge=0.5)
    )
    assert result.breakdown.deterministic == 0.5
    assert result.hard_fails == ()


def test_grade_result_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        ScoreBreakdown(deterministic=0.5, judge=0.5, bogus=1)  # type: ignore[call-arg]
