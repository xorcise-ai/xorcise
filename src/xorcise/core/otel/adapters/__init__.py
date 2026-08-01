"""Agent-agnostic trace normalization framework (part-island). Turns per-agent RAW OTLP
FlatSpan[] into the product-facing AgentEvent[] via AgentTraceAdapter implementations."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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
from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.adapters.base import AdapterContext
from xorcise.core.otel.adapters.registry import select
from xorcise.core.otel.flatten import FlatLogRecord, FlatSpan, flatten, flatten_logs

# Normalization is a versioned projection independently of each harness adapter's mapping version.
# Including it in RunEventsView.adapter_version makes persisted caches rebuild when shared assembly
# semantics (such as cross-batch ordering) change, even if the selected adapter itself did not.
NORMALIZER_VERSION = "2"


def projection_version(adapter_version: str) -> str:
    return f"{adapter_version}+normalizer.{NORMALIZER_VERSION}"


def _extract_record(
    record: TraceRecord | Mapping[str, Any],
) -> tuple[int, Any, datetime | None] | None:
    """Extract seq, payload and optional server-side receipt time from a RAW record.

    A structurally broken record (missing `seq`/`payload`, or an unexpected shape) yields
    `None` rather than raising `KeyError`/`AttributeError` — the caller skips it, matching
    `flatten()`'s TOTAL contract.
    """
    try:
        if isinstance(record, Mapping):
            received = record.get("received_at")
            if isinstance(received, str):
                try:
                    received = datetime.fromisoformat(received.replace("Z", "+00:00"))
                except ValueError:
                    received = None
            if received is not None and not isinstance(received, datetime):
                received = None
            return record["seq"], record["payload"], received
        return record.seq, record.payload, record.received_at
    except (KeyError, AttributeError):
        return None


def _decode_payload(payload: Any) -> Any:
    """`payload` may already be a dict, or an opaque JSON string; decode defensively.

    A malformed JSON string yields `None` (caller skips that record's spans) rather
    than raising — flatten() is TOTAL and this stays TOTAL to match it.
    """
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None
    return payload


def _error_event(ctx: AdapterContext, exc: Exception) -> AgentEvent:
    """The single `error` AgentEvent standing in for a contained adapter exception."""
    created = ctx.created_at
    ts = created if created.tzinfo is not None else created.replace(tzinfo=UTC)
    return AgentEvent(
        run_id=ctx.run_id,
        id="adapter-error",
        ts=ts,
        source_agent=ctx.source_agent,
        kind=AgentEventKind.error,
        title="adapter error",
        body=str(exc)[:200],
        severity="error",
        status="error",
        raw_ref=RawTraceRef(run_id=ctx.run_id, raw_seq=-1, span_id=""),
    )


def normalize_run(
    records: list[TraceRecord] | list[Mapping[str, Any]],
    ctx: AdapterContext,
    *,
    since: int = -1,
    log_records: Sequence[TraceRecord | Mapping[str, Any]] | None = None,
) -> RunEventsView:
    """RAW trace records (+ optional RAW LOG records) -> the `RunEventsView` page. TOTAL:
    an adapter exception never propagates — it is contained as one `error` AgentEvent + an
    `adapter_error` warning.

    `since` mirrors the `/runs/{id}/events?since=` cursor; `records` is expected to already be
    filtered to `seq > since` by the caller. `log_records` are the run's RAW OTLP logs — the
    adapter maps them (e.g. Claude Code's assistant_response) and they merge chronologically.
    """
    spans: list[FlatSpan] = []
    seqs: list[int] = []
    trace_receipts: dict[int, datetime] = {}
    for record in records:
        extracted = _extract_record(record)
        if extracted is None:
            continue
        seq, raw_payload, received_at = extracted
        seqs.append(seq)
        if received_at is not None:
            trace_receipts[seq] = received_at
        payload = _decode_payload(raw_payload)
        if payload is None:
            continue
        spans.extend(flatten(payload, raw_seq=seq))

    log_flats: list[FlatLogRecord] = []
    log_seqs: list[int] = []
    log_receipts: dict[int, datetime] = {}
    for record in log_records or []:
        extracted = _extract_record(record)
        if extracted is None:
            continue
        seq, raw_payload, received_at = extracted
        log_seqs.append(seq)
        if received_at is not None:
            log_receipts[seq] = received_at
        payload = _decode_payload(raw_payload)
        if payload is None:
            continue
        log_flats.extend(flatten_logs(payload, raw_seq=seq))

    adapter, fallback = select(ctx.source_agent, spans)

    warnings: list[AdapterWarning]
    log_events: list[AgentEvent] = []
    try:
        events = adapter.normalize(spans, ctx)
        log_events = adapter.normalize_logs(log_flats, ctx)
    except Exception as exc:  # total: adapter errors must never reach callers/routes
        events = [_error_event(ctx, exc)]
        log_events = []
        warnings = [AdapterWarning(code="adapter_error", message=str(exc)[:200])]
    else:
        warnings = []

    def with_receipt(event: AgentEvent) -> AgentEvent:
        receipts = log_receipts if event.raw_ref.signal == "log" else trace_receipts
        return event.model_copy(update={"received_at": receipts.get(event.raw_ref.raw_seq)})

    events = [with_receipt(event) for event in events]
    log_events = [with_receipt(event) for event in log_events]

    def order_key(event: AgentEvent) -> tuple[float, str, int, str]:
        # This view is the agent narrative, so its own clock is authoritative for chronology.
        # Receipt time says when XORCISE learned about an event, not when it happened: a delayed
        # export must not move a prompt behind its response. The presentation layer retains
        # received_at and uses it only as one-way evidence when interleaving server-clock infra.
        return (
            event.ts.timestamp(),
            event.raw_ref.signal,
            event.raw_ref.raw_seq,
            event.id,
        )

    events = sorted(events, key=order_key)
    # Merge log-derived events chronologically — but ONLY when logs exist, so a span-only run's
    # ordering (and its golden) is byte-identical to before.
    if log_events:
        # The user prompt can arrive from BOTH the interaction span and the user_prompt log; drop
        # the log copy when a span already carried that exact prompt so it never shows twice.
        span_user_prompts = {
            e.body for e in events if e.kind == AgentEventKind.message and e.role == "user"
        }
        log_events = [
            e
            for e in log_events
            if not (
                e.kind == AgentEventKind.message
                and e.role == "user"
                and e.body in span_user_prompts
            )
        ]
        events = sorted(events + log_events, key=order_key)

    counts: dict[str, int] = {
        "total": len(events),
        "unknown": sum(1 for e in events if e.kind == AgentEventKind.unknown),
    }
    for event in events:
        key = f"by_kind.{event.kind.value}"
        counts[key] = counts.get(key, 0) + 1

    next_since = max(seqs, default=-1)
    next_cursor = EventCursor(
        trace_seq=next_since,
        log_seq=max(log_seqs, default=-1),
    )

    return RunEventsView(
        run_id=ctx.run_id,
        source_agent=ctx.source_agent,
        adapter_name=adapter.name,
        adapter_version=projection_version(adapter.version),
        fallback=fallback,
        next_since=next_since,
        next_cursor=next_cursor,
        counts=counts,
        warnings=tuple(warnings),
        events=tuple(events),
    )
