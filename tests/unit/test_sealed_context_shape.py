from __future__ import annotations

import pytest

from xorcise.core.contracts.evidence import SealedContext


@pytest.mark.unit
def test_sealed_context_defaults_empty():
    ctx = SealedContext(run_id="r1")
    assert ctx.trace_present is True
    assert ctx.artifacts == {} and ctx.otel_stats == {} and ctx.observed_facts == {}


@pytest.mark.unit
def test_sealed_context_carries_three_sources():
    ctx = SealedContext(
        run_id="r1",
        trace_ref="t",
        artifacts={"flag": "XORCISE{x}"},
        otel_stats={"turn-count": 12},
        observed_facts={"flag-submitted": True},
    )
    assert ctx.artifacts["flag"] == "XORCISE{x}"
    assert ctx.otel_stats["turn-count"] == 12
    assert ctx.observed_facts["flag-submitted"] is True
