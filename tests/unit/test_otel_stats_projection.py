from __future__ import annotations

import pytest

from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.stats import project_otel_stats


@pytest.mark.unit
def test_project_otel_stats_counts_records():
    recs = [TraceRecord(run_id="r1", seq=i, payload="span") for i in range(1, 4)]
    stats = project_otel_stats(recs)
    assert stats["turn-count"] == 3
    assert stats["span-payload-count"] == 3


@pytest.mark.unit
def test_project_otel_stats_empty_trace_is_deterministic():
    assert project_otel_stats([]) == {"turn-count": 0, "span-payload-count": 0}
