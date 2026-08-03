# tests/unit/test_agent_events_cache.py
from __future__ import annotations

import json

import pytest

from xorcise.core import runs
from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.store import SqliteTraceStore


@pytest.fixture(autouse=True)
def _clear(migrated_home):
    # NOTE (deviation from the plan's literal fixture): clear_cache() now writes to the
    # agent_events table, so this autouse fixture MUST depend on migrated_home to
    # force pytest to set XORCISE_HOME + migrate to head before clear_cache() runs. Without
    # this dependency, pytest instantiates autouse fixtures before explicitly-requested ones,
    # so clear_cache() would hit the *real* default XORCISE_HOME (~/.xorcise/xorcise.db) —
    # which lacks agent_events pre-0026 and is a live dev DB either way.
    from xorcise.core.rest import events_view

    events_view.clear_cache()
    yield
    events_view.clear_cache()


def _otlp(span_id: str, name: str) -> dict[str, object]:
    return {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "scope": {"name": "t"},
                        "spans": [
                            {
                                "spanId": span_id,
                                "name": name,
                                "startTimeUnixNano": "1700000000000000000",
                                "attributes": [{"key": "command", "value": {"stringValue": "ls"}}],
                            }
                        ],
                    }
                ]
            }
        ]
    }


def _seed(run_id: str, spans: list[tuple[int, str, str]], source_agent: str = "generic") -> None:
    runs.create_run(
        run_id=run_id, agent_id="a1", mission="c", budget_seconds=60, source_agent=source_agent
    )
    st = SqliteTraceStore()
    for seq, sid, name in spans:
        st.append(TraceRecord(run_id=run_id, seq=seq, payload=json.dumps(_otlp(sid, name))))


def test_cache_built_once_and_reused(migrated_home, monkeypatch):
    from xorcise.core.otel.adapters import normalize_run
    from xorcise.core.rest import events_view

    _seed("r1", [(0, "s0", "shell.exec")])
    calls = {"n": 0}
    real = normalize_run

    def _spy(records, ctx, **kw):
        calls["n"] += 1
        return real(records, ctx, **kw)

    monkeypatch.setattr(events_view, "normalize_run", _spy)
    events_view.events_since("r1", -1)  # miss -> build + store
    events_view.events_since("r1", 0)  # DB cache hit -> no rebuild
    assert calls["n"] == 1


def test_new_records_invalidate(migrated_home, monkeypatch):
    from xorcise.core.otel.adapters import normalize_run
    from xorcise.core.rest import events_view

    _seed("r2", [(0, "s0", "shell.exec")])
    calls = {"n": 0}
    real = normalize_run

    def _spy(records, ctx, **kw):
        calls["n"] += 1
        return real(records, ctx, **kw)

    monkeypatch.setattr(events_view, "normalize_run", _spy)
    events_view.events_since("r2", -1)
    st = SqliteTraceStore()
    st.append(TraceRecord(run_id="r2", seq=1, payload=json.dumps(_otlp("s1", "assistant.msg"))))
    events_view.events_since("r2", -1)  # max_seq changed -> rebuild
    assert calls["n"] == 2


def test_adapter_version_bump_invalidates(migrated_home):
    from xorcise.core.db import session_scope
    from xorcise.core.otel.store.agent_events import SqliteAgentEventStore
    from xorcise.core.otel.store.models import AgentEventRunRow
    from xorcise.core.rest import events_view

    _seed("r3", [(0, "s0", "shell.exec")])
    events_view.events_since("r3", -1)  # builds; stores the selected adapter's version
    staleness = SqliteAgentEventStore().get_staleness("r3")
    assert staleness is not None
    current_version = staleness[1]
    # simulate a stale cache from an older adapter version:
    with session_scope() as s:
        header = s.get(AgentEventRunRow, "r3")
        assert header is not None
        header.adapter_version = "0"
    events_view.events_since("r3", -1)  # version mismatch -> rebuild to the current version
    rebuilt = SqliteAgentEventStore().get_staleness("r3")
    assert rebuilt is not None and rebuilt[1] == current_version


def test_regenerate_from_raw_equals_served(migrated_home):
    """Canonical-safety guard: the cached (served) view == a fresh normalize-from-RAW view."""
    from xorcise.core.otel.adapters import normalize_run
    from xorcise.core.rest import events_view

    _seed("r4", [(0, "s0", "shell.exec"), (1, "s1", "assistant.msg")])
    served = events_view.events_since("r4", -1)  # from cache (built on first read)
    ctx = events_view._ctx_for("r4")
    fresh = normalize_run(SqliteTraceStore().read("r4"), ctx)  # regenerate straight from RAW
    # NOTE (deviation from the plan's literal assertion): events_since() recomputes `counts`
    # over its page in a {kind: n} shape, distinct from normalize_run's/`_full_view`'s
    # {"total", "unknown", "by_kind.*"} shape (pre-existing behavior, unchanged by this
    # ticket) — so `served.model_dump()` can never equal a whole-run view's model_dump() on
    # `counts`. The real regenerate==served guarantee is that the CACHED whole-run view
    # (_full_view, untouched by events_since's page transform) is byte-for-byte the same as a
    # fresh normalize_run() over RAW — assert that directly, plus the served page's events still
    # matching the fresh regenerate.
    assert events_view._full_view("r4").model_dump() == fresh.model_dump()
    assert [e.id for e in served.events] == [e.id for e in fresh.events]
    assert served.next_since == fresh.next_since


def test_grader_never_imports_the_cache():
    import ast
    from pathlib import Path

    import xorcise.core.rest.grade_assembly as ga

    src = Path(ga.__file__).read_text()
    assert "agent_events" not in src, "grader must never read the agent_events projection cache"
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "otel.store.agent_events" not in node.module
