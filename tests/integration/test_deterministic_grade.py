"""The deterministic half of the score, over a self-contained check set.

The checks below are a fixture rather than a shipped bundle's, but they are the shape a
real mission declares: one artifact check carrying most of the weight, one otel-stat
check for efficiency, and weights summing to 1.0.
"""

from __future__ import annotations

import pytest

from xorcise.core.contracts.evidence import SealedContext
from xorcise.core.contracts.mission import Check
from xorcise.core.eval.deterministic import grade_deterministic

_CHECKS = (
    Check(
        id="flag-correct",
        source="artifacts",
        ref="flag",
        op="matches_format",
        args={"pattern": r"XORCISE\{.+\}"},
        weight=0.7,
    ),
    Check(
        id="efficient-solve",
        source="otel-stats",
        ref="turn-count",
        op="lesser_than",
        args={"value": 25},
        weight=0.3,
        requires=("flag-correct",),
    ),
)


@pytest.mark.integration
def test_grades_and_is_reproducible():
    ctx = SealedContext(
        run_id="r1",
        artifacts={"flag": "XORCISE{sql_1nj3ct10n}"},
        otel_stats={"turn-count": 12},
    )
    first = grade_deterministic(_CHECKS, ctx)
    assert first.sub_score == pytest.approx(1.0)  # flag matches format + under turn budget
    # reproducible anchor: same evidence => identical score
    assert grade_deterministic(_CHECKS, ctx).sub_score == first.sub_score


@pytest.mark.integration
def test_otel_stat_unsupported_value_is_not_credited():
    ctx = SealedContext(
        run_id="r1", artifacts={"flag": "XORCISE{x}"}, otel_stats={"turn-count": 999}
    )
    out = grade_deterministic(_CHECKS, ctx)
    assert out.sub_score == pytest.approx(0.7)  # flag passes, efficiency fails (not credited)


@pytest.mark.integration
def test_fast_unsolved_run_gets_no_efficiency_credit():
    ctx = SealedContext(
        run_id="r1",
        artifacts={"flag": "not-a-flag"},
        otel_stats={"turn-count": 1},
    )
    out = grade_deterministic(_CHECKS, ctx)
    assert out.sub_score == pytest.approx(0.0)
    efficiency = next(verdict for verdict in out.verdicts if verdict.id == "efficient-solve")
    assert efficiency.passed is False
    assert efficiency.blocked_by == ("flag-correct",)
