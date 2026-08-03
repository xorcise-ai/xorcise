# tests/unit/test_agent_events_store.py
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from xorcise.core.contracts.agent_event import (
    AdapterWarning,
    AgentEvent,
    AgentEventKind,
    EventCursor,
    RawTraceRef,
    RunEventsView,
)
from xorcise.core.otel.store.agent_events import SqliteAgentEventStore


def _event(
    run_id: str, ordinal: int, *, kind: AgentEventKind = AgentEventKind.message, **over: Any
) -> AgentEvent:
    fields: dict[str, Any] = {
        "run_id": run_id,
        "id": f"e{ordinal}",
        "ts": datetime(2026, 7, 5, 0, 0, ordinal, tzinfo=UTC),
        "received_at": datetime(2026, 7, 5, 1, 0, ordinal, tzinfo=UTC),
        "source_agent": "openhands",
        "kind": kind,
        "role": "agent",
        "title": f"t{ordinal}",
        "body": f"b{ordinal}",
        "data": {"k": "v", "n": str(ordinal)},
        "raw_ref": RawTraceRef(run_id=run_id, raw_seq=ordinal, span_id=f"s{ordinal}"),
    }
    fields.update(over)
    return AgentEvent(**fields)


def _view(
    run_id: str, events: list[AgentEvent], *, max_seq: int, log_max_seq: int = -1
) -> RunEventsView:
    return RunEventsView(
        run_id=run_id,
        source_agent="openhands",
        adapter_name="openhands",
        adapter_version="1",
        fallback=False,
        next_since=max_seq,
        next_cursor=EventCursor(trace_seq=max_seq, log_seq=log_max_seq),
        counts={"total": len(events)},
        warnings=(AdapterWarning(code="x", message="m"),),
        events=tuple(events),
    )


def test_put_read_view_roundtrip_is_faithful(migrated_home):
    # Store-level fidelity: reconstruct-from-rows == what was put in, across None-able fields,
    # subkind, data, group_id, duration_ms, status, severity, receipt time.
    store = SqliteAgentEventStore()
    events = [
        _event("r1", 0, kind=AgentEventKind.terminal_command, body="ls", subkind="exec"),
        _event(
            "r1",
            1,
            kind=AgentEventKind.message,
            group_id="g1",
            duration_ms=12,
            status="ok",
            severity="warning",
        ),
    ]
    view = _view("r1", events, max_seq=1)
    store.put("r1", view, 1)
    got = store.read_view("r1")
    assert got is not None
    assert got.model_dump() == view.model_dump()  # byte-for-byte reconstruction
    assert got.events[0].received_at == events[0].received_at


def test_read_page_paginates_by_raw_seq(migrated_home):
    store = SqliteAgentEventStore()
    events = [_event("r2", i) for i in range(3)]  # raw_seq 0, 1, 2
    store.put("r2", _view("r2", events, max_seq=2), 2)
    page = store.read_page("r2", 0)  # raw_seq > 0 -> events 1, 2
    assert page is not None
    assert [e.raw_ref.raw_seq for e in page.events] == [1, 2]
    assert page.next_since == 2  # from the header, not the page
    assert page.adapter_name == "openhands"


def test_read_page_uses_independent_trace_and_log_cursors(migrated_home):
    store = SqliteAgentEventStore()
    trace = _event("mixed", 0)
    log = _event(
        "mixed",
        1,
        id="log-0",
        raw_ref=RawTraceRef(run_id="mixed", raw_seq=0, span_id="", signal="log"),
    )
    view = _view("mixed", [trace, log], max_seq=0, log_max_seq=0)
    store.put("mixed", view, source_max_seq=0, log_max_seq=0)

    page = store.read_page("mixed", trace_since=0, log_since=-1)

    assert page is not None
    assert [event.id for event in page.events] == ["log-0"]
    assert page.next_cursor == EventCursor(trace_seq=0, log_seq=0)


def test_get_staleness_and_overwrite(migrated_home):
    store = SqliteAgentEventStore()
    assert store.get_staleness("r3") is None
    store.put("r3", _view("r3", [_event("r3", 0)], max_seq=0), 0)
    # staleness key is now (adapter_name, adapter_version, source_max_seq, log_max_seq)
    assert store.get_staleness("r3") == ("openhands", "1", 0, 0)
    # overwrite replaces header + rows (no duplicate/leftover rows)
    store.put("r3", _view("r3", [_event("r3", 0), _event("r3", 1)], max_seq=1), 1, 2)
    got = store.read_view("r3")
    assert got is not None and len(got.events) == 2
    assert store.get_staleness("r3") == ("openhands", "1", 1, 2)


def test_drop_and_clear(migrated_home):
    store = SqliteAgentEventStore()
    store.put("r1", _view("r1", [_event("r1", 0)], max_seq=0), 0)
    store.put("r2", _view("r2", [_event("r2", 0)], max_seq=0), 0)
    store.drop("r1")
    assert store.read_view("r1") is None and store.read_view("r2") is not None
    store.clear()
    assert store.read_view("r2") is None


def test_tables_exist_at_head(migrated_home):
    # migrated_home migrates to head (0027) — both tables must be queryable.
    assert SqliteAgentEventStore().read_view("nope") is None
    assert SqliteAgentEventStore().get_staleness("nope") is None
