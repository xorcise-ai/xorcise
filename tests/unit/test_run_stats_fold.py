"""fold_run_stats — the per-run telemetry fold. The parametrized fixture cases are the
regression guard against the "flat sum returns zero for Codex" trap: each real capture uses a
distinct token-key schema, and all three must yield non-zero input/output."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from xorcise.core.contracts.agent_event import AgentEvent, AgentEventKind, RawTraceRef
from xorcise.core.otel.adapters import normalize_run
from xorcise.core.otel.adapters.base import AdapterContext
from xorcise.core.otel.run_stats import fold_run_stats

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "otlp"


def _metric(data: dict[str, str], ts: datetime = _T0) -> AgentEvent:
    return AgentEvent(
        run_id="r1",
        id=f"m{ts.timestamp()}{data}",
        ts=ts,
        source_agent="x",
        kind=AgentEventKind.metric,
        title="usage",
        data=data,
        raw_ref=RawTraceRef(run_id="r1", raw_seq=0, span_id=""),
    )


def _kind(
    kind: AgentEventKind,
    *,
    duration_ms: int | None = None,
    status: Literal["ok", "error"] | None = None,
) -> AgentEvent:
    return AgentEvent(
        run_id="r1",
        id=f"e{kind}{duration_ms}{status}",
        ts=_T0,
        source_agent="x",
        kind=kind,
        title="t",
        duration_ms=duration_ms,
        status=status,
        raw_ref=RawTraceRef(run_id="r1", raw_seq=0, span_id=""),
    )


def test_openhands_and_claude_keys_sum() -> None:
    events = [
        _metric({"input_tokens": "100", "output_tokens": "20", "cache_read_input_tokens": "5"})
    ]
    s = fold_run_stats(events, created_at=_T0, completed_at=None)
    assert s.tokens.input == 100
    assert s.tokens.output == 20
    assert s.tokens.cache_read == 5
    assert s.tokens.total == 120  # computed, not read


def test_codex_count_keys_sum() -> None:
    events = [
        _metric(
            {
                "input_token_count": "300",
                "output_token_count": "40",
                "cached_token_count": "7",
                "reasoning_token_count": "9",
            }
        )
    ]
    s = fold_run_stats(events, created_at=_T0, completed_at=None)
    assert s.tokens.input == 300
    assert s.tokens.output == 40
    assert s.tokens.cache_read == 7
    assert s.tokens.reasoning == 9
    assert s.tokens.total == 340


def test_model_calls_counts_token_metrics_not_ttft() -> None:
    events = [
        _metric({"input_token_count": "1", "output_token_count": "1"}),
        _metric({"duration_ms": "50"}),  # a ttft-style metric with no tokens — not a model call
    ]
    s = fold_run_stats(events, created_at=_T0, completed_at=None)
    assert s.counts.model_calls == 1


def test_counts_and_timing() -> None:
    events = [
        _kind(AgentEventKind.tool_call, duration_ms=10),
        _kind(AgentEventKind.terminal_command, duration_ms=90),
        _kind(AgentEventKind.finding),
        _kind(AgentEventKind.error, status="error"),
    ]
    s = fold_run_stats(
        events, created_at=_T0, completed_at=datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)
    )
    assert s.counts.tool_calls == 2
    assert s.counts.findings == 1
    assert s.counts.errors == 1
    assert s.counts.events_total == 4
    assert s.counts.by_kind["tool_call"] == 1
    assert s.timing.longest_tool_ms == 90
    assert s.timing.elapsed_seconds == 30.0


def test_empty_and_malformed_never_raise() -> None:
    events = [_metric({"input_tokens": "not-a-number"})]
    s = fold_run_stats(events, created_at=_T0, completed_at=None)
    assert s.tokens.input == 0
    assert s.tokens.total == 0
    assert fold_run_stats([], created_at=_T0, completed_at=None).counts.events_total == 0


@pytest.mark.parametrize("name", ["openhands", "claude_code", "codex"])
def test_each_real_harness_yields_nonzero_tokens(name: str) -> None:
    # The harness adapters self-register on import but are pulled lazily (plane isolation), so the
    # projection falls back to `generic` (no token metrics) unless we load them first — exactly what
    # events_view does before it projects.
    from xorcise.core.harness_adapters import load_adapters

    load_adapters()
    doc = json.loads((_FIXTURES / f"{name}_real_run.json").read_text())
    ctx = AdapterContext(
        run_id=doc.get("run_id", "r1"),
        source_agent=name if name != "claude_code" else "claude-code",
        mission_id="fixture",
        created_at=_T0,
    )
    view = normalize_run(doc["records"], ctx, log_records=doc.get("log_records"))
    s = fold_run_stats(view.events, created_at=_T0, completed_at=None)
    assert s.tokens.input > 0, f"{name}: input tokens folded to zero"
    assert s.tokens.output > 0, f"{name}: output tokens folded to zero"
    assert s.tokens.total == s.tokens.input + s.tokens.output
    assert s.counts.model_calls > 0
