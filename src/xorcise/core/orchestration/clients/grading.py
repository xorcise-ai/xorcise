"""JudgePort adapters.

StubJudge: canned 50/50 verdict (the seam stand-in, still bound to the canned JudgePortContract).
EvalJudge: the REAL grader — turns a thin GradeRequest into a SealedContext via
delivery-injected readers, then runs the 50/50 grade in xorcise.core.eval. Cross-module reads are
injected because orchestration cannot import runs/runcontrol/otel (the RunControlDeps pattern); the
delivery layer assembles the SealedContext + resolves the mission's checks/rubric. eval.grade is
the inward (application layer -> part) call; eval never imports back.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from xorcise.core.contracts.evidence import SealedContext
from xorcise.core.contracts.grading import GradeRequest, GradeResult, ScoreBreakdown
from xorcise.core.contracts.mission import Check, RubricCriterion
from xorcise.core.eval.grade import grade as eval_grade
from xorcise.core.eval.judge import (
    DEFAULT_JUDGE_SPAN_MAX_TOKENS,
    DEFAULT_JUDGE_TRANSCRIPT_MAX_TOKENS,
    JudgeModel,
    TokenCounter,
)
from xorcise.core.orchestration.ports import JudgePort


class StubJudge(JudgePort):
    def grade(self, request: GradeRequest) -> GradeResult:
        return GradeResult(
            run_id=request.run_id,
            overall=0.5,
            breakdown=ScoreBreakdown(deterministic=0.5, judge=0.5),
            trace_ref=request.trace_ref,
        )


@dataclass(frozen=True)
class EvalJudgeDeps:
    """Cross-module reads are INJECTED (the delivery layer builds them) — orchestration cannot
    import runs/runcontrol/otel, so the rest layer assembles the SealedContext + resolves the
    mission's checks/rubric from the installed manifest and passes the readers in."""

    sealed_context_for: Callable[[GradeRequest], SealedContext]
    checks_for: Callable[[GradeRequest], tuple[Check, ...]]
    rubric_for: Callable[[GradeRequest], tuple[RubricCriterion, ...]]
    model: JudgeModel | None = None
    max_transcript_tokens: int = DEFAULT_JUDGE_TRANSCRIPT_MAX_TOKENS
    max_span_tokens: int = DEFAULT_JUDGE_SPAN_MAX_TOKENS
    count_tokens: TokenCounter | None = None


class EvalJudge(JudgePort):
    """The real JudgePort: assemble the sealed context, then run the 50/50 grade in eval."""

    def __init__(self, deps: EvalJudgeDeps) -> None:
        self._d = deps

    def grade(self, request: GradeRequest) -> GradeResult:
        ctx = self._d.sealed_context_for(request)
        return eval_grade(
            ctx,
            checks=self._d.checks_for(request),
            rubric=self._d.rubric_for(request),
            model=self._d.model,
            max_transcript_tokens=self._d.max_transcript_tokens,
            max_span_tokens=self._d.max_span_tokens,
            count_tokens=self._d.count_tokens,
        )
