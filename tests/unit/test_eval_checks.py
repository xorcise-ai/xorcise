from __future__ import annotations

import pytest

from xorcise.core.contracts.evidence import SealedContext
from xorcise.core.contracts.mission import Check
from xorcise.core.eval.checks import effective_weights, run_check


@pytest.mark.unit
def test_run_check_passes_and_carries_value():
    ctx = SealedContext(run_id="r", artifacts={"flag": "XORCISE{x}"})
    c = Check(
        id="flag",
        source="artifacts",
        ref="flag",
        op="equals",
        args={"expected": "XORCISE{x}"},
        weight=1.0,
    )
    v = run_check(c, ctx)
    assert v.passed is True and v.value == "XORCISE{x}" and v.id == "flag" and v.weight == 1.0


@pytest.mark.unit
def test_run_check_fails_when_op_false():
    ctx = SealedContext(run_id="r", otel_stats={"turn-count": 99})
    c = Check(id="eff", source="otel-stats", ref="turn-count", op="lesser_than", args={"value": 25})
    v = run_check(c, ctx)
    assert v.passed is False and v.value == 99


@pytest.mark.unit
def test_effective_weights_equal_split_when_none_declared():
    cs = [Check(id=str(i), source="artifacts", ref="f", op="observed") for i in range(4)]
    assert effective_weights(cs) == [0.25, 0.25, 0.25, 0.25]


@pytest.mark.unit
def test_effective_weights_uses_declared():
    cs = [
        Check(id="a", source="artifacts", ref="f", op="observed", weight=0.7),
        Check(id="b", source="artifacts", ref="f", op="observed", weight=0.3),
    ]
    assert effective_weights(cs) == [0.7, 0.3]


@pytest.mark.unit
def test_effective_weights_empty():
    assert effective_weights([]) == []


# ═══ Defensive dispatch (the "grading stuck forever" fix) ═══
# The contract now rejects unknown ops/arg shapes at ingest, but an ALREADY-installed record can
# predate that gate — model_construct bypasses validation the same way a stored legacy record does.


@pytest.mark.unit
def test_run_check_unknown_op_fails_with_error_instead_of_raising():
    ctx = SealedContext(run_id="r", artifacts={"flag": "XORCISE{x}"})
    c = Check.model_construct(
        id="legacy", source="artifacts", ref="flag", op="regex", args={}, weight=None
    )
    v = run_check(c, ctx)
    assert v.passed is False and v.value is None
    assert v.error is not None and "unknown op 'regex'" in v.error


@pytest.mark.unit
def test_run_check_bad_args_fail_with_error_instead_of_raising():
    """A known op with a wrong arg shape (TypeError at dispatch) is the same hang class."""
    ctx = SealedContext(run_id="r", artifacts={"flag": "XORCISE{x}"})
    c = Check.model_construct(
        id="legacy", source="artifacts", ref="flag", op="equals", args={"expectd": "X"}, weight=None
    )
    v = run_check(c, ctx)
    assert v.passed is False
    assert v.error is not None and "TypeError" in v.error


@pytest.mark.unit
def test_run_check_healthy_verdict_has_no_error():
    ctx = SealedContext(run_id="r", artifacts={"flag": "XORCISE{x}"})
    c = Check(id="flag", source="artifacts", ref="flag", op="observed")
    assert run_check(c, ctx).error is None


@pytest.mark.unit
def test_grade_deterministic_isolates_an_unexecutable_check():
    """One legacy bad check must not sink the others: it scores 0 with a disclosed error while
    the healthy check still credits its equal split."""
    from xorcise.core.eval.deterministic import grade_deterministic

    ctx = SealedContext(run_id="r", artifacts={"flag": "XORCISE{x}"})
    good = Check(id="ok", source="artifacts", ref="flag", op="observed")
    bad = Check.model_construct(
        id="legacy", source="artifacts", ref="flag", op="regex", args={}, weight=None
    )
    out = grade_deterministic([good, bad], ctx)
    assert out.sub_score == 0.5
    assert out.verdicts[0].passed is True and out.verdicts[0].error is None
    assert out.verdicts[1].passed is False and out.verdicts[1].error is not None
