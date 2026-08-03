# src/xorcise/core/rest/events_view.py
"""Read-side helpers for the events API — generate-on-read + raw drill-down.

The route handlers (rest/routers/runs.py) lazy-import this module so the otel display plane
stays off role:control's boot path (plane-isolation invariant). This module owns:
  * building an AdapterContext from a run's snapshot,
  * a WHOLE-RUN normalize behind a persistent read-through cache — the projection is
    a pure function of the immutable RAW trace store, so a stable (adapter_name,
    adapter_version, source_max_seq) triple means a stable view,
  * slicing the whole-run view by the `since` cursor into a page,
  * mapping an event_id back to its source RAW OTLP span(s) for the drill-down.

Everything is derived + rebuildable from the RAW `traces` store; nothing here is canonical.
The versioned `agent_events` cache lives behind this same seam — a cache hit is
exactly the stored `normalize_run` output, so regenerate-from-RAW always equals served.
Grading never touches this module (guarded — see tests/unit/test_grader_isolation.py).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from xorcise.core import runs
from xorcise.core.contracts.agent_event import EventCursor, RunEventsView
from xorcise.core.contracts.telemetry import TraceRecord

# Register the built-in agent adapters (collect plane). events_view is the read-path composition
# point — the core framework never imports agent adapters; the concrete adapters live in the
# harness_adapters tier and self-register when load_adapters() imports them (called below, after
# the imports, to stay E402-clean). Still lazy: events_view is itself lazy-imported by the route
# handler, so this stays off role:control's boot path (plane-isolation invariant).
from xorcise.core.harness_adapters import load_adapters
from xorcise.core.otel.adapters import normalize_run
from xorcise.core.otel.adapters.base import AdapterContext
from xorcise.core.otel.adapters.registry import select
from xorcise.core.otel.flatten import FlatSpan, flatten
from xorcise.core.otel.store import SqliteLogStore, SqliteTraceStore
from xorcise.core.otel.store.agent_events import SqliteAgentEventStore

# Collect-plane registration (see the import note above). Idempotent; placed after imports so the
# module stays E402-clean — still lazy, since events_view is lazy-imported by the route handler.
load_adapters()


def clear_cache() -> None:
    """Drop the persistent projection cache (tests + adapter-version bumps)."""
    SqliteAgentEventStore().clear()


def _ctx_for(run_id: str) -> AdapterContext:
    """AdapterContext from the run's snapshot; a default 'generic' ctx for an unknown run."""
    entry = runs.get(run_id)
    if entry is not None:
        return AdapterContext(
            run_id=run_id,
            source_agent=entry.source_agent,
            mission_id=entry.mission,
            created_at=entry.created_at,
        )
    return AdapterContext(
        run_id=run_id, source_agent="generic", mission_id="", created_at=datetime.now(UTC)
    )


def _flatten_records(records: list[TraceRecord]) -> list[FlatSpan]:
    """Cheap flatten (for adapter selection); mirrors normalize_run's decode+flatten. Total."""
    spans: list[FlatSpan] = []
    for r in records:
        raw = r.payload
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                continue
        else:
            payload = raw
        spans.extend(flatten(payload, raw_seq=r.seq))
    return spans


def _ensure_fresh(run_id: str) -> None:
    """Rebuild + persist the NORMALIZED projection if the cache is missing/stale for the current
    adapter + RAW. Staleness key = (adapter_name, adapter_version, source_max_seq); on a
    mismatch the run's header + per-event rows are replaced via normalize_run (pure) from RAW. The
    RAW store is canonical; this cache is derived + always rebuildable."""
    records = SqliteTraceStore().read(run_id)
    logs = SqliteLogStore().read(run_id)  # the RAW logs signal, merged into the view
    max_seq = max((r.seq for r in records), default=-1)
    log_max_seq = max((r.seq for r in logs), default=-1)
    ctx = _ctx_for(run_id)
    adapter, _ = select(ctx.source_agent, _flatten_records(records))
    store = SqliteAgentEventStore()
    if store.get_staleness(run_id) == (adapter.name, adapter.version, max_seq, log_max_seq):
        return
    store.put(run_id, normalize_run(records, ctx, log_records=logs), max_seq, log_max_seq)


def _full_view(run_id: str) -> RunEventsView:
    """The whole-run projection, reconstructed from the normalized cache (header + per-event
    rows). Ensures the cache is fresh first. Falls back to a direct normalize on the (transient)
    chance the header is absent, so a caller always gets a stable view."""
    _ensure_fresh(run_id)
    view = SqliteAgentEventStore().read_view(run_id)
    if view is None:
        return normalize_run(
            SqliteTraceStore().read(run_id),
            _ctx_for(run_id),
            log_records=SqliteLogStore().read(run_id),
        )
    return view


def events_since(
    run_id: str,
    since: int = -1,
    *,
    cursor: EventCursor | None = None,
) -> RunEventsView:
    """Read an incremental page with independent trace/log sequence coordinates.

    `since` remains as the legacy trace-only entry point. New callers pass `cursor`; this prevents
    a trace batch numbered N from hiding a later log batch that independently received number N.
    """
    requested = cursor or EventCursor(trace_seq=since, log_seq=since)
    _ensure_fresh(run_id)
    page = SqliteAgentEventStore().read_page(run_id, requested.trace_seq, requested.log_seq)
    if page is None:
        return normalize_run(
            SqliteTraceStore().read(run_id),
            _ctx_for(run_id),
            log_records=SqliteLogStore().read(run_id),
        )
    return page


def raw_for_event(run_id: str, event_id: str) -> dict[str, Any] | None:
    """Map an event_id to its source RAW OTLP span(s). None if the event_id is unknown."""
    full = _full_view(run_id)
    event = next((e for e in full.events if e.id == event_id), None)
    if event is None:
        return None
    ref = event.raw_ref
    return {
        "event_id": event_id,
        "raw_ref": ref.model_dump(mode="json"),
        "spans": (_raw_spans(run_id, ref.raw_seq, ref.span_id) if ref.signal == "trace" else []),
        "logs": _raw_logs(run_id, ref.raw_seq) if ref.signal == "log" else [],
    }


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _as_dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _raw_spans(run_id: str, raw_seq: int, span_id: str) -> list[dict[str, Any]]:
    """The RAW OTLP span dict(s) at `raw_seq` whose spanId == span_id. Total (never raises)."""
    matched: list[dict[str, Any]] = []
    for rec in SqliteTraceStore().read_since(run_id, raw_seq - 1):
        if rec.seq != raw_seq:
            continue
        try:
            payload = json.loads(rec.payload)
        except (ValueError, TypeError):
            continue
        for rs in _as_list(_as_dict(payload).get("resourceSpans")):
            for ss in _as_list(_as_dict(rs).get("scopeSpans")):
                for sp in _as_list(_as_dict(ss).get("spans")):
                    if isinstance(sp, dict) and sp.get("spanId") == span_id:
                        matched.append(sp)
    return matched


def _raw_logs(run_id: str, raw_seq: int) -> list[dict[str, Any]]:
    """The RAW OTLP log-record dicts in one persisted log export batch. Total."""
    matched: list[dict[str, Any]] = []
    for rec in SqliteLogStore().read_since(run_id, raw_seq - 1):
        if rec.seq != raw_seq:
            continue
        try:
            payload = json.loads(rec.payload)
        except (ValueError, TypeError):
            continue
        for resource_logs in _as_list(_as_dict(payload).get("resourceLogs")):
            for scope_logs in _as_list(_as_dict(resource_logs).get("scopeLogs")):
                matched.extend(
                    record
                    for record in _as_list(_as_dict(scope_logs).get("logRecords"))
                    if isinstance(record, dict)
                )
    return matched
