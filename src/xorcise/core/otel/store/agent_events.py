"""agent_events cache store: the NORMALIZED, versioned, rebuildable projection cache.

Persists the whole-run AgentEvent projection as a run header (`agent_event_runs`) + one row per
event (`agent_events`) behind the events_view read seam — so /events reads are cheap AND events
are queryable/inspectable at the DB level (kind, body, raw_seq, ...). DERIVED + rebuildable from
`traces` — NEVER canonical; the grader never reads it (guarded). Keyed by run_id; a
(adapter_name, adapter_version, source_max_seq) mismatch means stale -> the read path rebuilds via
normalize_run and replaces the run's header + rows.

Fidelity invariant (guard test): reconstruct-from-rows (`read_view`) == the `normalize_run` output
that produced them. Every AgentEvent field is a first-class column except the small variable
`data` map (JSON) — so `read_view` round-trips byte-for-byte via `AgentEvent.model_validate`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import and_, delete, or_, select

from xorcise.core.contracts.agent_event import (
    AdapterWarning,
    AgentEvent,
    EventCursor,
    RunEventsView,
)
from xorcise.core.db import session_scope
from xorcise.core.otel.store.models import AgentEventRow, AgentEventRunRow


def _event_to_row(event: AgentEvent, ordinal: int) -> AgentEventRow:
    """Serialize one AgentEvent into its row. `data` -> JSON; `raw_ref` -> flat columns; every
    other field is a column. `ts` is stored ISO-8601 for an exact, sortable round-trip."""
    return AgentEventRow(
        run_id=event.run_id,
        ord=ordinal,
        event_id=event.id,
        ts=event.ts.isoformat(),
        received_at=event.received_at.isoformat() if event.received_at is not None else None,
        source_agent=event.source_agent,
        kind=event.kind.value,
        subkind=event.subkind,
        role=event.role,
        title=event.title,
        body=event.body,
        data_json=json.dumps(dict(event.data)),
        group_id=event.group_id,
        parent_id=event.parent_id,
        actor_id=event.actor_id,
        actor_name=event.actor_name,
        actor_role=event.actor_role,
        conversation_id=event.conversation_id,
        duration_ms=event.duration_ms,
        status=event.status,
        severity=event.severity,
        raw_seq=event.raw_ref.raw_seq,
        span_id=event.raw_ref.span_id,
        trace_id=event.raw_ref.trace_id,
        signal=event.raw_ref.signal,
    )


def _row_to_event(row: AgentEventRow) -> AgentEvent:
    """Reconstruct one AgentEvent from its row. Uses `model_validate` so pydantic re-parses/
    re-validates (ts ISO -> datetime, kind -> enum, Literals) into the exact original value."""
    return AgentEvent.model_validate(
        {
            "run_id": row.run_id,
            "id": row.event_id,
            "ts": row.ts,
            "received_at": row.received_at,
            "source_agent": row.source_agent,
            "kind": row.kind,
            "subkind": row.subkind,
            "role": row.role,
            "title": row.title,
            "body": row.body,
            "data": json.loads(row.data_json),
            "group_id": row.group_id,
            "parent_id": row.parent_id,
            "actor_id": row.actor_id,
            "actor_name": row.actor_name,
            "actor_role": row.actor_role,
            "conversation_id": row.conversation_id,
            "duration_ms": row.duration_ms,
            "status": row.status,
            "severity": row.severity,
            "raw_ref": {
                "run_id": row.run_id,
                "raw_seq": row.raw_seq,
                "span_id": row.span_id,
                "trace_id": row.trace_id,
                "signal": row.signal,
            },
        }
    )


class SqliteAgentEventStore:
    """Normalized projection cache: one `agent_event_runs` header + N `agent_events` rows/run."""

    def get_staleness(self, run_id: str) -> tuple[str, str, int, int] | None:
        """The (adapter_name, adapter_version, source_max_seq, log_max_seq) key, or None if
        uncached. The logs max-seq is in the key so the cache rebuilds when the logs
        signal grows too, not just traces."""
        with session_scope() as s:
            h = s.get(AgentEventRunRow, run_id)
            if h is None:
                return None
            return (h.adapter_name, h.adapter_version, h.source_max_seq, h.log_max_seq)

    def _view(
        self, h: AgentEventRunRow, events: list[AgentEvent], *, counts: dict[str, int]
    ) -> RunEventsView:
        warnings = tuple(AdapterWarning.model_validate(w) for w in json.loads(h.warnings_json))
        return RunEventsView(
            run_id=h.run_id,
            source_agent=h.source_agent,
            adapter_name=h.adapter_name,
            adapter_version=h.adapter_version,
            fallback=h.fallback,
            next_since=h.next_since,
            next_cursor=EventCursor(trace_seq=h.next_since, log_seq=h.next_log_since),
            counts=counts,
            warnings=warnings,
            events=tuple(events),
        )

    def read_view(self, run_id: str) -> RunEventsView | None:
        """The whole-run view reconstructed from the header + all rows (ordered). Uses the stored
        run-level `counts` so the result is byte-identical to the `normalize_run` output."""
        with session_scope() as s:
            h = s.get(AgentEventRunRow, run_id)
            if h is None:
                return None
            rows = (
                s.execute(
                    select(AgentEventRow)
                    .where(AgentEventRow.run_id == run_id)
                    .order_by(AgentEventRow.ord)
                )
                .scalars()
                .all()
            )
            return self._view(h, [_row_to_event(r) for r in rows], counts=json.loads(h.counts_json))

    def read_page(
        self, run_id: str, trace_since: int, log_since: int | None = None
    ) -> RunEventsView | None:
        """Read a signal-aware incremental page, ordered by whole-run projection ordinal.

        `log_since=None` preserves the old one-cursor behavior for internal/third-party callers;
        first-party callers always pass both independently numbered signal cursors.
        """
        effective_log_since = trace_since if log_since is None else log_since
        with session_scope() as s:
            h = s.get(AgentEventRunRow, run_id)
            if h is None:
                return None
            rows = (
                s.execute(
                    select(AgentEventRow)
                    .where(
                        AgentEventRow.run_id == run_id,
                        or_(
                            and_(
                                AgentEventRow.signal == "trace",
                                AgentEventRow.raw_seq > trace_since,
                            ),
                            and_(
                                AgentEventRow.signal == "log",
                                AgentEventRow.raw_seq > effective_log_since,
                            ),
                        ),
                    )
                    .order_by(AgentEventRow.ord)
                )
                .scalars()
                .all()
            )
            events = [_row_to_event(r) for r in rows]
            counts: dict[str, int] = {}
            for e in events:
                counts[e.kind.value] = counts.get(e.kind.value, 0) + 1
            return self._view(h, events, counts=counts)

    def put(
        self, run_id: str, view: RunEventsView, source_max_seq: int, log_max_seq: int = 0
    ) -> None:
        """Replace the run's header + event rows with `view` (one transaction). Whole-run rebuild
        — grouping can change when RAW grows, so rows are deleted + reinserted, never appended.
        `log_max_seq` folds the logs signal into the staleness key."""
        with session_scope() as s:
            s.execute(delete(AgentEventRow).where(AgentEventRow.run_id == run_id))
            s.execute(delete(AgentEventRunRow).where(AgentEventRunRow.run_id == run_id))
            s.add(
                AgentEventRunRow(
                    run_id=run_id,
                    adapter_name=view.adapter_name,
                    adapter_version=view.adapter_version,
                    source_max_seq=source_max_seq,
                    log_max_seq=log_max_seq,
                    source_agent=view.source_agent,
                    fallback=view.fallback,
                    next_since=view.next_since,
                    next_log_since=view.next_cursor.log_seq,
                    counts_json=json.dumps(dict(view.counts)),
                    warnings_json=json.dumps([w.model_dump(mode="json") for w in view.warnings]),
                    created_at=datetime.now(UTC),
                )
            )
            for ordinal, event in enumerate(view.events):
                s.add(_event_to_row(event, ordinal))

    def drop(self, run_id: str) -> None:
        with session_scope() as s:
            s.execute(delete(AgentEventRow).where(AgentEventRow.run_id == run_id))
            s.execute(delete(AgentEventRunRow).where(AgentEventRunRow.run_id == run_id))

    def clear(self) -> None:
        with session_scope() as s:
            s.execute(delete(AgentEventRow))
            s.execute(delete(AgentEventRunRow))
