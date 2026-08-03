from __future__ import annotations

import pytest

from xorcise.core.contracts.evidence import SealedContext
from xorcise.core.eval.resolvers import RESOLVERS


@pytest.fixture
def ctx():
    return SealedContext(
        run_id="r1",
        artifacts={"flag": "XORCISE{x}"},
        otel_stats={"turn-count": 12},
        observed_facts={"flag-submitted": True},
    )


@pytest.mark.unit
def test_resolvers_read_each_source(ctx):
    assert RESOLVERS["artifacts"](ctx, "flag") == "XORCISE{x}"
    assert RESOLVERS["otel-stats"](ctx, "turn-count") == 12
    assert RESOLVERS["observed-facts"](ctx, "flag-submitted") is True


@pytest.mark.unit
def test_missing_ref_resolves_to_none(ctx):
    assert RESOLVERS["artifacts"](ctx, "absent") is None
    assert RESOLVERS["otel-stats"](ctx, "absent") is None
    assert RESOLVERS["observed-facts"](ctx, "absent") is None
