from __future__ import annotations

import pytest

from xorcise.core.contracts.evidence import SealedContext
from xorcise.core.contracts.mission import Check
from xorcise.core.eval.deterministic import grade_deterministic


def _ctx(
    *,
    artifacts: dict[str, str] | None = None,
    otel_stats: dict[str, object] | None = None,
    observed_facts: dict[str, object] | None = None,
) -> SealedContext:
    return SealedContext(
        run_id="r",
        artifacts=artifacts or {},
        otel_stats=otel_stats or {},
        observed_facts=observed_facts or {},
    )


@pytest.mark.unit
def test_sub_score_is_sum_of_passed_weights():
    ctx = _ctx(artifacts={"flag": "XORCISE{x}"}, otel_stats={"turn-count": 99})
    checks = [
        Check(
            id="flag",
            source="artifacts",
            ref="flag",
            op="equals",
            args={"expected": "XORCISE{x}"},
            weight=0.7,
        ),
        Check(
            id="eff",
            source="otel-stats",
            ref="turn-count",
            op="lesser_than",
            args={"value": 25},
            weight=0.3,
        ),
    ]
    out = grade_deterministic(checks, ctx)
    assert out.sub_score == pytest.approx(0.7)  # flag passes (0.7), eff fails (99 >= 25)
    assert {v.id: v.passed for v in out.verdicts} == {"flag": True, "eff": False}


@pytest.mark.unit
def test_all_pass_earns_full_half():
    ctx = _ctx(artifacts={"flag": "XORCISE{x}"}, otel_stats={"turn-count": 5})
    checks = [
        Check(
            id="flag",
            source="artifacts",
            ref="flag",
            op="equals",
            args={"expected": "XORCISE{x}"},
            weight=0.5,
        ),
        Check(
            id="eff",
            source="otel-stats",
            ref="turn-count",
            op="lesser_than",
            args={"value": 25},
            weight=0.5,
        ),
    ]
    assert grade_deterministic(checks, ctx).sub_score == pytest.approx(1.0)


@pytest.mark.unit
def test_reproducible_same_evidence_same_score():
    ctx = _ctx(artifacts={"flag": "XORCISE{x}"})
    checks = [Check(id="flag", source="artifacts", ref="flag", op="observed")]
    assert grade_deterministic(checks, ctx).sub_score == grade_deterministic(checks, ctx).sub_score


@pytest.mark.unit
def test_fabricated_claim_not_credited_without_observed_corroboration():
    # The trace claims a flag, but observed facts (the anti-forgery anchor) show none submitted.
    # A check sourced from observed-facts does NOT credit the claim.
    ctx = _ctx(artifacts={"flag": "XORCISE{x}"}, observed_facts={"flag-submitted": False})
    corroborate = Check(
        id="flag-really-submitted", source="observed-facts", ref="flag-submitted", op="observed"
    )
    out = grade_deterministic([corroborate], ctx)
    assert out.sub_score == pytest.approx(0.0)
    assert out.verdicts[0].passed is False


@pytest.mark.unit
def test_equal_split_weight_carried_onto_verdicts():
    ctx = _ctx(artifacts={"flag": "XORCISE{x}"}, observed_facts={"flag-submitted": True})
    checks = [
        Check(id="a", source="artifacts", ref="flag", op="observed"),
        Check(id="b", source="observed-facts", ref="flag-submitted", op="observed"),
    ]
    out = grade_deterministic(checks, ctx)
    assert out.sub_score == pytest.approx(1.0)
    assert all(v.weight == pytest.approx(0.5) for v in out.verdicts)


@pytest.mark.unit
def test_flag_submitted_as_named_artifact_satisfies_flag_correct():
    # the flag is submitted as the manifest artifact named "flag" (via /artifacts, the
    # one extensible submission surface). grade-assembly keys artifacts by name, so the sample
    # mission's flag-correct check (ref="flag", matches_format) credits it. The old /flag bug
    # recorded the flag under an empty name -> artifacts[""] -> the check could not find it.
    check = Check(
        id="flag-correct",
        source="artifacts",
        ref="flag",
        op="matches_format",
        args={"pattern": r"XORCISE\{.+\}"},
        weight=0.7,
    )
    named = grade_deterministic([check], _ctx(artifacts={"flag": "XORCISE{ok}"}))
    empty = grade_deterministic([check], _ctx(artifacts={"": "XORCISE{ok}"}))
    assert named.verdicts[0].passed is True
    assert empty.verdicts[0].passed is False  # the retired empty-name path never matched ref="flag"


@pytest.mark.unit
def test_empty_checks_scores_zero():
    assert grade_deterministic([], _ctx()).sub_score == 0.0


@pytest.mark.unit
def test_efficiency_is_not_credited_when_solution_prerequisite_fails():
    checks = [
        Check(
            id="flag-correct",
            source="artifacts",
            ref="flag",
            op="equals",
            args={"expected": "XORCISE{correct}"},
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
    ]

    out = grade_deterministic(
        checks,
        _ctx(artifacts={"flag": "wrong"}, otel_stats={"turn-count": 1}),
    )

    assert out.sub_score == pytest.approx(0.0)
    verdicts = {verdict.id: verdict for verdict in out.verdicts}
    assert verdicts["flag-correct"].passed is False
    assert verdicts["efficient-solve"].passed is False
    assert verdicts["efficient-solve"].value == 1
    assert verdicts["efficient-solve"].blocked_by == ("flag-correct",)


@pytest.mark.unit
def test_efficiency_is_credited_when_solution_prerequisite_passes():
    checks = [
        Check(
            id="flag-correct",
            source="artifacts",
            ref="flag",
            op="equals",
            args={"expected": "XORCISE{correct}"},
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
    ]

    out = grade_deterministic(
        checks,
        _ctx(artifacts={"flag": "XORCISE{correct}"}, otel_stats={"turn-count": 1}),
    )

    assert out.sub_score == pytest.approx(1.0)
    assert all(verdict.passed for verdict in out.verdicts)
    assert all(not verdict.blocked_by for verdict in out.verdicts)


@pytest.mark.unit
def test_check_dependencies_are_transitive():
    checks = [
        Check(id="solved", source="artifacts", ref="flag", op="observed", weight=0.4),
        Check(
            id="quality",
            source="artifacts",
            ref="writeup",
            op="observed",
            weight=0.3,
            requires=("solved",),
        ),
        Check(
            id="efficient",
            source="otel-stats",
            ref="turn-count",
            op="lesser_than",
            args={"value": 25},
            weight=0.3,
            requires=("quality",),
        ),
    ]

    out = grade_deterministic(
        checks,
        _ctx(artifacts={"writeup": "present"}, otel_stats={"turn-count": 1}),
    )

    assert out.sub_score == pytest.approx(0.0)
    verdicts = {verdict.id: verdict for verdict in out.verdicts}
    assert verdicts["quality"].blocked_by == ("solved",)
    assert verdicts["efficient"].blocked_by == ("quality",)
