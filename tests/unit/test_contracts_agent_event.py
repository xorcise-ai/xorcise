# tests/unit/test_contracts_agent_event.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.agent_event import (
    AdapterWarning,
    AgentEvent,
    AgentEventKind,
    EventCursor,
    RawTraceRef,
    RunEventsView,
)


def _event(**over: object) -> AgentEvent:
    base = dict(
        run_id="r1",
        id="span1",
        ts=datetime(2026, 7, 3, tzinfo=UTC),
        source_agent="generic",
        kind=AgentEventKind.terminal_command,
        title="ran ls",
        raw_ref=RawTraceRef(run_id="r1", raw_seq=0, span_id="span1"),
    )
    base.update(over)
    return AgentEvent(**base)  # type: ignore[arg-type]


def test_kind_is_closed_18_value_enum():
    assert len(list(AgentEventKind)) == 18
    assert AgentEventKind.message == "message"
    assert AgentEventKind.unknown == "unknown"


def test_agent_event_constructs_with_defaults():
    ev = _event()
    assert ev.kind == "terminal_command"
    assert ev.role == "agent" and ev.severity == "info"
    assert ev.body == "" and dict(ev.data) == {}
    assert ev.raw_ref.raw_seq == 0


def test_agent_event_is_frozen_and_forbids_extra():
    ev = _event()
    with pytest.raises(ValidationError):
        ev.title = "changed"  # frozen
    with pytest.raises(ValidationError):
        _event(bogus=1)  # extra=forbid


def test_run_events_view_holds_event_tuple_and_flags():
    view = RunEventsView(
        run_id="r1",
        source_agent="generic",
        adapter_name="generic",
        adapter_version="1",
        fallback=True,
        next_since=0,
        counts={"total": 1},
        warnings=(AdapterWarning(code="unknown_span", message="unseen"),),
        events=(_event(),),
    )
    assert view.events[0].id == "span1"
    assert view.fallback is True and view.warnings[0].count == 1
    assert view.next_cursor == EventCursor()
