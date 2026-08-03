from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from xorcise.core.contracts.evidence import SealedContext
from xorcise.core.contracts.grading import CheckVerdict, GradeResult, ScoreBreakdown
from xorcise.core.contracts.mission import Check, RubricCriterion
from xorcise.core.eval.grade import grade

RUBRIC = (RubricCriterion(id="r1", text="did the thing", weight=1.0),)
CHECKS = (
    Check(
        id="flag",
        source="artifacts",
        ref="flag",
        op="equals",
        args={"expected": "XORCISE{x}"},
        weight=1.0,
    ),
)


class _Model:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def score(self, messages: Sequence[tuple[str, str]]) -> str:
        return json.dumps(self._payload)


@pytest.mark.unit
def test_grade_result_check_breakdown_defaults_empty_and_carries():
    r = GradeResult(run_id="r", overall=0.0, breakdown=ScoreBreakdown())
    assert r.check_breakdown == ()
    v = CheckVerdict(
        id="c", source="artifacts", ref="flag", op="observed", value="X", passed=True, weight=1.0
    )
    r2 = GradeResult(run_id="r", overall=0.5, breakdown=ScoreBreakdown(), check_breakdown=(v,))
    assert r2.check_breakdown[0].id == "c"


@pytest.mark.unit
def test_combine_5050_when_judge_ok():
    ctx = SealedContext(run_id="r", trace_ref="t", artifacts={"flag": "XORCISE{x}"})
    # Per-criterion decomposition: one call per criterion, replying with a single-criterion payload.
    model = _Model({"score": 0.5, "reason": "ok"})
    result = grade(ctx, checks=CHECKS, rubric=RUBRIC, model=model)
    assert result.breakdown.deterministic == pytest.approx(1.0)  # flag passes
    assert result.breakdown.judge == pytest.approx(0.5)
    assert result.overall == pytest.approx(0.5 * 1.0 + 0.5 * 0.5)  # 0.75
    assert result.judge_status == "ok"
    assert {c.id for c in result.check_breakdown} == {"flag"}
    assert {c.criterion_id for c in result.judge_breakdown} == {"r1"}
    assert result.trace_ref == "t"
    # The SHARED prompt prefix is preserved once; the criterion id rides its per-criterion prompt.
    assert result.judge_prompt is not None
    assert any("r1" in (c.criterion_prompt or "") for c in result.judge_breakdown)


@pytest.mark.unit
def test_grade_forwards_transcript_budget_to_judge():
    ctx = SealedContext(run_id="r", trace_ref="t", transcript=("y" * 300,))
    result = grade(
        ctx,
        checks=(),
        rubric=RUBRIC,
        model=_Model({}),
        max_transcript_tokens=50,
        count_tokens=len,
    )
    assert result.judge_status == "unavailable"
    assert "pre-flight cap" in (result.judge_detail or "").lower()


@pytest.mark.unit
def test_combine_judge_degraded_deterministic_stands():
    ctx = SealedContext(run_id="r", trace_ref="t", artifacts={"flag": "XORCISE{x}"})
    result = grade(ctx, checks=CHECKS, rubric=RUBRIC, model=None)  # no model
    assert result.breakdown.deterministic == pytest.approx(1.0)
    assert result.breakdown.judge == 0.0
    assert result.judge_status == "model-not-configured"
    # overall reflects the deterministic half only (judge contributes 0); disclosed via judge_status
    assert result.overall == pytest.approx(0.5)


@pytest.mark.unit
def test_partial_judge_exposes_conservative_overall_interval_and_coverage():
    rubric = (
        RubricCriterion(id="seen", text="observable", weight=0.25),
        RubricCriterion(id="hidden", text="requires missing telemetry", weight=0.75),
    )

    class _PerCriterionModel:
        def score(self, messages: Sequence[tuple[str, str]]) -> str:
            if "— seen:" in messages[-1][1]:
                return '{"score": 1.0, "reason": "shown"}'
            return '{"verdict": "unknown", "reason": "missing event class"}'

    ctx = SealedContext(
        run_id="r",
        trace_ref="t",
        artifacts={"flag": "XORCISE{x}"},
        telemetry_gaps=("tool: this harness does not export tool events",),
    )
    result = grade(ctx, checks=CHECKS, rubric=rubric, model=_PerCriterionModel())
    assert result.judge_status == "partial"
    assert result.breakdown.judge == pytest.approx(0.25)
    assert result.judge_upper == pytest.approx(1.0)
    assert result.judge_coverage == pytest.approx(0.25)
    assert result.overall == pytest.approx(0.625)
    assert result.overall_upper == pytest.approx(1.0)
