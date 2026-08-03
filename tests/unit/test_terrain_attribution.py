from __future__ import annotations

from datetime import UTC, datetime

import pytest

from xorcise.core.contracts.agent_event import AgentEvent, AgentEventKind, RawTraceRef
from xorcise.core.runs.terrain_attribution import attributable_event_ids

pytestmark = pytest.mark.unit


def _ev(eid: str, kind: AgentEventKind) -> AgentEvent:
    return AgentEvent(
        run_id="r1",
        id=eid,
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        source_agent="a",
        kind=kind,
        title=eid,
        body=eid,
        raw_ref=RawTraceRef(run_id="r1", raw_seq=1, span_id="s"),
    )


def test_attributable_event_ids_excludes_conversation_and_debug_kinds():
    events = [
        _ev("e-msg", AgentEventKind.message),  # conversation -> not attributable
        _ev("e-think", AgentEventKind.thinking),  # conversation -> not attributable
        _ev("e-metric", AgentEventKind.metric),  # debug -> not attributable
        _ev("e-unknown", AgentEventKind.unknown),  # debug -> not attributable
        _ev("e-cmd", AgentEventKind.terminal_command),  # action -> attributable
    ]
    assert attributable_event_ids(events) == {"e-cmd"}
