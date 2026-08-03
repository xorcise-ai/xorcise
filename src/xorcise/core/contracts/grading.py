"""Grading wire DTOs (LEAF). The evaluator job/result shapes (the 50/50 grade).

Rubric/deterministic-check schemas are owned by the eval engine.
Imports nothing internal (stdlib + pydantic only).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GradeRequest(_Frozen):
    """What the server hands the evaluator: artifacts + raw-trace ref + observed facts."""

    run_id: str
    trace_ref: str  # run_id-keyed pointer into the trace store
    artifacts: tuple[str, ...] = ()  # submitted work-artifact refs (thin)
    observed_facts: tuple[str, ...] = ()  # facts corroborated from the trace (thin)
    rubric_ref: str | None = None  # rubric id; schema = grading story
    check_set_ref: str | None = None  # deterministic-check set id; = grading story


class ScoreBreakdown(_Frozen):
    deterministic: float = 0.0
    judge: float = 0.0


class CheckVerdict(_Frozen):
    """One deterministic check's effective outcome and resolved value.

    `blocked_by` names failed prerequisites. A raw operation may have succeeded while `passed`
    is false because a prerequisite prevented the check from earning credit."""

    id: str
    source: str
    ref: str
    op: str
    value: object = None
    passed: bool = False
    weight: float = 0.0
    # Defensive-grading disclosure (the deterministic twin of judge_status/judge_detail): non-None
    # when the check could not execute (unknown legacy op, resolver/op exception) — it then counts
    # as failed instead of crashing grading. Additive; default preserves the wire shape.
    error: str | None = None
    blocked_by: tuple[str, ...] = ()


class CriterionScore(_Frozen):
    """One rubric criterion's judge result — a SELF-CONTAINED snapshot.

    text/weight are copied from the rubric AS GRADED so the result survives later manifest
    mutation; criterion_id is only a join key back to RubricCriterion.id."""

    criterion_id: str
    text: str
    weight: float
    score: float
    reason: str
    # Per-criterion decomposition: each criterion is graded by its own isolated judge call.
    # `unobservable` is reserved for a declared platform evidence limitation; `error` means the
    # model failed the response contract after one retry. `unknown` remains readable for persisted
    # pre-change grades, but new grading never emits it. Neither non-scored state is renormalized
    # away: the official score uses the full rubric weight and GradeResult exposes an uncertainty
    # interval + evidence coverage.
    status: Literal["ok", "unobservable", "error", "unknown"] = "ok"
    criterion_prompt: str | None = None


class GradeResult(_Frozen):
    """The 50/50 evidence-anchored verdict."""

    run_id: str
    overall: float
    breakdown: ScoreBreakdown
    key_evidence: tuple[str, ...] = ()
    major_deductions: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    trace_ref: str | None = None
    hard_fails: tuple[str, ...] = ()
    # judge-half disclosure + per-criterion breakdown (additive; defaults preserve shape)
    judge_status: Literal["ok", "partial", "model-not-configured", "unavailable"] = "ok"
    judge_detail: str | None = None
    judge_breakdown: tuple[CriterionScore, ...] = ()
    # `overall` and `breakdown.judge` are conservative lower bounds: unscored rubric weight earns
    # no credit. These additive fields expose the best-case bound and how much rubric weight was
    # actually scored. None preserves compatibility with old persisted grades.
    overall_upper: float | None = None
    judge_upper: float | None = None
    judge_coverage: float | None = None
    # The SHARED judge prompt prefix (grading instructions + fenced evidence) fed to every
    # per-criterion call, preserved once for the results page (None if no judge ran). Each
    # criterion's own trailing prompt is on CriterionScore.criterion_prompt.
    judge_prompt: str | None = None
    # How many distilled transcript spans had their body capped (Lever 1) in the evidence the judge
    # saw — surfaced so the results page can disclose that some span bodies were truncated.
    spans_truncated: int = 0
    # the deterministic half's per-check verdicts (the structured "why" behind the score)
    check_breakdown: tuple[CheckVerdict, ...] = ()
