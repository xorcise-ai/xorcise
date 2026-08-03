# tests/unit/test_events_view.py
from __future__ import annotations

import json

import pytest

from xorcise.core import runs
from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.store import SqliteTraceStore


@pytest.fixture(autouse=True)
def _clear_events_cache(migrated_home):
    # clear_cache() now clears the DB-backed agent_events table, so this autouse
    # fixture must depend on migrated_home — otherwise pytest's "autouse before explicit"
    # ordering would run clear_cache() before XORCISE_HOME is pointed at the tmp DB, hitting
    # the real default XORCISE_HOME instead.
    from xorcise.core.rest import events_view

    events_view.clear_cache()
    yield
    events_view.clear_cache()


def _otlp(span_id: str, name: str, start_ns: int = 1_700_000_000_000_000_000) -> dict[str, object]:
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "x"}}]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "test.tracer"},
                        "spans": [
                            {
                                "spanId": span_id,
                                "name": name,
                                "startTimeUnixNano": str(start_ns),
                                "endTimeUnixNano": str(start_ns + 1),
                                "attributes": [{"key": "command", "value": {"stringValue": "ls"}}],
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _seed_run(run_id: str, source_agent: str = "generic") -> None:
    # create_run is the real path; if unavailable use repository directly.
    runs.create_run(
        run_id=run_id,
        agent_id="a1",
        mission="chal-x",
        budget_seconds=60,
        source_agent=source_agent,
    )


def _seed_traces(run_id: str, spans: list[tuple[int, str, str]]) -> None:
    store = SqliteTraceStore()
    for seq, span_id, name in spans:
        store.append(TraceRecord(run_id=run_id, seq=seq, payload=json.dumps(_otlp(span_id, name))))


def test_events_since_returns_full_run_then_slices(migrated_home):
    from xorcise.core.rest import events_view

    _seed_run("r1")
    _seed_traces("r1", [(0, "s0", "shell.exec"), (1, "s1", "assistant.msg")])

    full = events_view.events_since("r1", -1)
    assert full.run_id == "r1"
    assert full.source_agent == "generic"
    assert full.next_since == 1  # whole-run max seq
    assert len(full.events) == 2
    assert set(full.counts) and sum(full.counts.values()) == 2

    # slice: since=0 returns only events from records with seq > 0
    page = events_view.events_since("r1", 0)
    assert all(e.raw_ref.raw_seq > 0 for e in page.events)
    assert len(page.events) == 1
    assert page.next_since == 1  # unchanged — polling advances even when sliced
    assert sum(page.counts.values()) == 1

    # empty tail: since at the max returns no events but keeps next_since
    tail = events_view.events_since("r1", 1)
    assert tail.events == ()
    assert tail.next_since == 1


def test_events_since_unknown_run_is_empty_not_error(migrated_home):
    from xorcise.core.rest import events_view

    view = events_view.events_since("nope", -1)
    assert view.run_id == "nope"
    assert view.events == ()
    assert view.source_agent == "generic"  # default ctx for an unknown run


def test_memoized_by_run_id_and_max_seq(migrated_home, monkeypatch):
    from xorcise.core.otel.adapters import normalize_run
    from xorcise.core.rest import events_view

    _seed_run("r2")
    _seed_traces("r2", [(0, "s0", "shell.exec")])

    calls = {"n": 0}
    real = normalize_run

    def _spy(records, ctx, **kw):
        calls["n"] += 1
        return real(records, ctx, **kw)

    monkeypatch.setattr(events_view, "normalize_run", _spy)

    events_view.events_since("r2", -1)
    events_view.events_since("r2", 0)  # same max_seq -> served from memo
    assert calls["n"] == 1

    _seed_traces("r2", [(1, "s1", "assistant.msg")])  # max_seq changes -> recompute
    events_view.events_since("r2", -1)
    assert calls["n"] == 2


def test_raw_for_event_returns_source_span(migrated_home):
    from xorcise.core.rest import events_view

    _seed_run("r3")
    _seed_traces("r3", [(0, "sABC", "shell.exec")])
    full = events_view.events_since("r3", -1)
    ev = full.events[0]

    raw = events_view.raw_for_event("r3", ev.id)
    assert raw is not None
    assert raw["event_id"] == ev.id
    assert raw["raw_ref"]["span_id"] == "sABC"
    assert raw["raw_ref"]["raw_seq"] == 0
    assert isinstance(raw["spans"], list) and len(raw["spans"]) == 1
    assert raw["spans"][0]["spanId"] == "sABC"
    assert raw["spans"][0]["name"] == "shell.exec"


def test_raw_for_event_unknown_event_is_none(migrated_home):
    from xorcise.core.rest import events_view

    _seed_run("r4")
    _seed_traces("r4", [(0, "s0", "shell.exec")])
    assert events_view.raw_for_event("r4", "does-not-exist") is None


def test_raw_span_walk_tolerates_malformed_payload(migrated_home):
    from xorcise.core.rest import events_view

    _seed_run("r5")
    store = SqliteTraceStore()
    store.append(TraceRecord(run_id="r5", seq=0, payload="{not json"))  # malformed
    # must not raise; simply yields no events (and thus no raw match)
    view = events_view.events_since("r5", -1)
    assert view.next_since == 0  # seq still counted
    assert events_view.raw_for_event("r5", "anything") is None


def test_full_view_overwrites_stale_row_without_error(migrated_home):
    """A stale header (mismatched adapter_version + source_max_seq) is transparently
    rebuilt + overwritten on read — the normalized cache replaces the run's header + event rows
    (see also test_agent_events_cache.py::test_adapter_version_bump_invalidates /
    test_new_records_invalidate for the staleness-triple mismatches)."""
    from xorcise.core.db import session_scope
    from xorcise.core.otel.store.agent_events import SqliteAgentEventStore
    from xorcise.core.otel.store.models import AgentEventRunRow
    from xorcise.core.rest import events_view

    _seed_run("rp")
    _seed_traces("rp", [(0, "s0", "shell.exec")])

    # Pre-seed a stale header for this run_id (mismatched adapter_version + source_max_seq).
    with session_scope() as s:
        s.add(
            AgentEventRunRow(
                run_id="rp",
                adapter_name="generic",
                adapter_version="0",
                source_max_seq=-1,
                source_agent="generic",
                fallback=True,
                next_since=-1,
                counts_json="{}",
                warnings_json="[]",
            )
        )

    view = events_view.events_since("rp", -1)  # must not raise; rebuilds + overwrites the header
    assert view.next_since == 0

    staleness = SqliteAgentEventStore().get_staleness("rp")
    assert staleness is not None
    assert staleness[2] == 0  # source_max_seq overwritten to the current max_seq, not left stale
