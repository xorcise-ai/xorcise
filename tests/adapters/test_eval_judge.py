from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from xorcise.core.contracts.evidence import SealedContext
from xorcise.core.contracts.grading import GradeRequest
from xorcise.core.contracts.mission import Check, RubricCriterion
from xorcise.core.eval.judge import JudgeModel
from xorcise.core.orchestration.clients.grading import EvalJudge, EvalJudgeDeps


class _Model:
    def score(self, messages: Sequence[tuple[str, str]]) -> str:
        # Per-criterion decomposition: the model is called once per criterion and replies with a
        # single-criterion payload (not a map keyed by criterion id).
        return json.dumps({"score": 1.0, "reason": "ok"})


def _deps(*, model: JudgeModel | None = None) -> EvalJudgeDeps:
    return EvalJudgeDeps(
        sealed_context_for=lambda req: SealedContext(
            run_id=req.run_id, trace_ref=req.trace_ref, artifacts={"flag": "XORCISE{x}"}
        ),
        checks_for=lambda req: (
            Check(
                id="flag",
                source="artifacts",
                ref="flag",
                op="equals",
                args={"expected": "XORCISE{x}"},
                weight=1.0,
            ),
        ),
        rubric_for=lambda req: (RubricCriterion(id="r1", text="did it", weight=1.0),),
        model=model,
    )


@pytest.mark.adapters
def test_eval_judge_grades_via_injected_readers():
    judge = EvalJudge(_deps(model=_Model()))
    result = judge.grade(GradeRequest(run_id="r1", trace_ref="t"))
    assert result.run_id == "r1"
    assert result.breakdown.deterministic == pytest.approx(1.0)
    assert result.breakdown.judge == pytest.approx(1.0)
    assert result.overall == pytest.approx(1.0)
    assert result.judge_status == "ok"


@pytest.mark.adapters
def test_eval_judge_over_budget_transcript_is_unavailable():
    deps = EvalJudgeDeps(
        sealed_context_for=lambda req: SealedContext(
            run_id=req.run_id,
            trace_ref=req.trace_ref,
            artifacts={"flag": "XORCISE{x}"},
            transcript=("z" * 400,),
        ),
        checks_for=lambda req: (),
        rubric_for=lambda req: (RubricCriterion(id="r1", text="did it", weight=1.0),),
        model=_Model(),
        max_transcript_tokens=50,
        count_tokens=len,
    )
    result = EvalJudge(deps).grade(GradeRequest(run_id="r1", trace_ref="t"))
    assert result.judge_status == "unavailable"
    assert "pre-flight cap" in (result.judge_detail or "").lower()


@pytest.mark.adapters
def test_eval_judge_degrades_without_model():
    judge = EvalJudge(_deps(model=None))
    result = judge.grade(GradeRequest(run_id="r1", trace_ref="t"))
    assert result.judge_status == "model-not-configured"
    assert result.breakdown.deterministic == pytest.approx(1.0)  # deterministic half stands
