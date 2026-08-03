"""Pure projection over the sealed RAW trace -> deterministic stats.

These are TRACE-DERIVED and therefore agent-forgeable -- a CONVENIENCE source, not the
anti-forgery anchor (that is observed facts; see runs/observed.py). The v1 stat vocabulary is
deliberately small: the coarse counts the persisted records already support.
"""

from __future__ import annotations

from collections.abc import Sequence

from xorcise.core.contracts.telemetry import TraceRecord


def project_otel_stats(records: Sequence[TraceRecord]) -> dict[str, object]:
    n = len(records)
    return {"turn-count": n, "span-payload-count": n}
