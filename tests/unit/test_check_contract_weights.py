from __future__ import annotations

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.mission import (
    Check,
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
)


def _manifest(*checks: Check) -> MissionManifest:
    return MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id="c", name="c", objective="o", type="lab"),
        environment=EnvironmentSpec(),
        checks=checks,
    )


def _chk(cid: str, weight: float | None = None) -> Check:
    return Check(id=cid, source="artifacts", ref="flag", op="observed", weight=weight)


@pytest.mark.unit
def test_new_check_fields():
    c = Check(
        id="flag-correct", source="artifacts", ref="flag", op="equals", args={"expected": "X"}
    )
    assert (c.source, c.ref, c.op, c.args["expected"]) == ("artifacts", "flag", "equals", "X")
    assert c.weight is None


@pytest.mark.unit
def test_weights_none_declared_is_ok():
    _manifest(_chk("a"), _chk("b"))  # equal-split resolved later; validator allows all-None


@pytest.mark.unit
def test_weights_all_declared_sum_one_ok():
    _manifest(_chk("a", 0.7), _chk("b", 0.3))


@pytest.mark.unit
def test_weights_mixed_is_error():
    with pytest.raises(ValidationError, match="ALL declare weight or NONE"):
        _manifest(_chk("a", 0.7), _chk("b"))


@pytest.mark.unit
def test_weights_declared_sum_not_one_is_error():
    with pytest.raises(ValidationError, match="sum to 1.0"):
        _manifest(_chk("a", 0.5), _chk("b", 0.3))


@pytest.mark.unit
def test_empty_checks_ok():
    _manifest()  # no checks is valid (the scoring math has a fallback)


@pytest.mark.unit
def test_weight_bounds_enforced():
    with pytest.raises(ValidationError):
        Check(id="a", source="artifacts", ref="f", op="observed", weight=0.0)  # gt=0
    with pytest.raises(ValidationError):
        Check(id="a", source="artifacts", ref="f", op="observed", weight=1.5)  # le=1


@pytest.mark.unit
def test_check_dependencies_accept_valid_graph():
    manifest = _manifest(
        _chk("solved", 0.7),
        Check(
            id="efficient",
            source="otel-stats",
            ref="turn-count",
            op="lesser_than",
            args={"value": 25},
            weight=0.3,
            requires=("solved",),
        ),
    )
    assert manifest.checks[1].requires == ("solved",)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("checks", "message"),
    [
        ((_chk("same", 0.5), _chk("same", 0.5)), "check IDs must be unique"),
        (
            (
                _chk("solved", 0.7),
                Check(
                    id="efficient",
                    source="otel-stats",
                    ref="turn-count",
                    op="lesser_than",
                    args={"value": 25},
                    weight=0.3,
                    requires=("missing",),
                ),
            ),
            "requires unknown check 'missing'",
        ),
        (
            (
                Check(
                    id="self",
                    source="artifacts",
                    ref="flag",
                    op="observed",
                    weight=1.0,
                    requires=("self",),
                ),
            ),
            "cannot require itself",
        ),
        (
            (
                Check(
                    id="a",
                    source="artifacts",
                    ref="a",
                    op="observed",
                    weight=0.5,
                    requires=("b",),
                ),
                Check(
                    id="b",
                    source="artifacts",
                    ref="b",
                    op="observed",
                    weight=0.5,
                    requires=("a",),
                ),
            ),
            "check dependency cycle",
        ),
    ],
)
def test_manifest_rejects_invalid_check_dependencies(
    checks: tuple[Check, ...], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _manifest(*checks)


@pytest.mark.unit
def test_check_rejects_duplicate_dependencies():
    with pytest.raises(ValidationError, match="requires contains duplicate"):
        Check(
            id="efficient",
            source="otel-stats",
            ref="turn-count",
            op="lesser_than",
            args={"value": 25},
            requires=("solved", "solved"),
        )


@pytest.mark.unit
def test_check_rejects_empty_dependency_id():
    with pytest.raises(ValidationError):
        Check(
            id="efficient",
            source="otel-stats",
            ref="turn-count",
            op="lesser_than",
            args={"value": 25},
            requires=("",),
        )
