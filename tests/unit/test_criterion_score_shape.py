from __future__ import annotations

import pytest

from xorcise.core.contracts.grading import CriterionScore, GradeResult, ScoreBreakdown


@pytest.mark.unit
def test_criterion_score_is_self_contained_snapshot():
    cs = CriterionScore(
        criterion_id="auth-bypass",
        text="Bypassed auth via SQLi",
        weight=0.4,
        score=0.8,
        reason="trace shows the UNION payload",
    )
    assert (cs.criterion_id, cs.text, cs.weight, cs.score) == (
        "auth-bypass",
        "Bypassed auth via SQLi",
        0.4,
        0.8,
    )


@pytest.mark.unit
def test_grade_result_judge_fields_default_to_ok_empty():
    r = GradeResult(run_id="r", overall=0.5, breakdown=ScoreBreakdown())
    assert r.judge_status == "ok"
    assert r.judge_detail is None
    assert r.judge_breakdown == ()


@pytest.mark.unit
def test_grade_result_carries_judge_breakdown_and_status():
    cs = CriterionScore(criterion_id="c1", text="t", weight=1.0, score=0.5, reason="why")
    r = GradeResult(
        run_id="r",
        overall=0.25,
        breakdown=ScoreBreakdown(deterministic=0.5, judge=0.0),
        judge_status="model-not-configured",
        judge_detail="no key",
        judge_breakdown=(cs,),
    )
    assert r.judge_status == "model-not-configured" and r.judge_breakdown[0].criterion_id == "c1"
