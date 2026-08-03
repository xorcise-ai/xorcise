"""The 50/50 combine — assemble the evidence-anchored GradeResult.

Pure over a SealedContext: runs the deterministic half + the judge half and
combines them. overall = 0.5*det + 0.5*judge when the judge is "ok"; when the judge degrades, its
half contributes 0.0 and the condition is disclosed via judge_status (the deterministic half always
stands). Every field ties back to recorded evidence (check + criterion breakdowns).
"""

from __future__ import annotations

from collections.abc import Sequence

from xorcise.core.contracts.evidence import SealedContext
from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown
from xorcise.core.contracts.mission import Check, RubricCriterion
from xorcise.core.eval.deterministic import grade_deterministic
from xorcise.core.eval.judge import (
    DEFAULT_JUDGE_SPAN_MAX_TOKENS,
    DEFAULT_JUDGE_TRANSCRIPT_MAX_TOKENS,
    JudgeModel,
    TokenCounter,
    grade_judge,
)


def grade(
    ctx: SealedContext,
    *,
    checks: Sequence[Check],
    rubric: Sequence[RubricCriterion],
    model: JudgeModel | None,
    max_transcript_tokens: int = DEFAULT_JUDGE_TRANSCRIPT_MAX_TOKENS,
    max_span_tokens: int = DEFAULT_JUDGE_SPAN_MAX_TOKENS,
    count_tokens: TokenCounter | None = None,
) -> GradeResult:
    det = grade_deterministic(checks, ctx)
    judge = grade_judge(
        rubric,
        ctx,
        model,
        max_transcript_tokens=max_transcript_tokens,
        max_span_tokens=max_span_tokens,
        count_tokens=count_tokens,
    )
    overall = 0.5 * det.sub_score + 0.5 * judge.sub_score
    overall_upper = 0.5 * det.sub_score + 0.5 * judge.upper_score
    return GradeResult(
        run_id=ctx.run_id,
        overall=overall,
        breakdown=ScoreBreakdown(deterministic=det.sub_score, judge=judge.sub_score),
        artifacts=tuple(sorted(ctx.artifacts)),
        trace_ref=ctx.trace_ref,
        judge_status=judge.status,
        judge_detail=judge.detail,
        judge_breakdown=judge.per_criterion,
        overall_upper=overall_upper,
        judge_upper=judge.upper_score,
        judge_coverage=judge.coverage,
        judge_prompt=judge.prompt,
        spans_truncated=judge.spans_truncated,
        check_breakdown=det.verdicts,
    )
