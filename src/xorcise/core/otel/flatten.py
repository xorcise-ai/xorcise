"""Flatten raw OTLP payloads to FlatSpan[] (UNIVERSAL display-plane helper).

The display-plane peer of decode.py: decode.py routes RAW spans at INGEST; flatten.py
turns the persisted RAW OTLP into a per-span struct every AgentTraceAdapter consumes, so
adapters never touch raw OTLP JSON. Pure + TOTAL: tolerates missing levels / unknown shapes
and NEVER raises (a malformed payload yields []). Protobuf is already normalized to this
canonical OTLP/JSON dict shape (camelCase) by decode.py at ingest, so this walks the dict
form for both JSON and protobuf origins. Imports stdlib only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FlatSpanEvent:
    """One OTLP span event (name + time + flattened attrs), for adapters. Internal.

    Some harnesses (e.g. Claude Code) carry their real content — prompts, shell commands,
    LLM output, token usage — in span *events*, not span attributes; this surfaces them."""

    name: str
    time_ns: int
    attrs: Mapping[str, str]


@dataclass(frozen=True)
class FlatSpan:
    """One OTLP span, flattened for adapters. Internal (not a wire contract)."""

    span_id: str
    parent_span_id: str
    trace_id: str
    name: str
    start_ns: int
    end_ns: int
    status_code: int
    attrs: Mapping[str, str]
    scope: str
    resource: Mapping[str, str]
    raw_seq: int = 0
    events: tuple[FlatSpanEvent, ...] = ()  # span events (default () — adapters opt in)


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return 0


def _scalar(value: Any) -> str | None:
    """An OTLP attribute value -> scalar string; complex values preserved as JSON; else None."""
    if not isinstance(value, dict):
        return None
    for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if value.get(k) is not None:
            return str(value[k])
    for k in ("arrayValue", "kvlistValue"):  # preserve losslessly rather than drop
        if k in value:
            try:
                return json.dumps(value[k], separators=(",", ":"))
            except (TypeError, ValueError):
                return None
    return None


def _flatten_attrs(attributes: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(attributes, list):
        return out
    for a in attributes:
        if not isinstance(a, dict):
            continue
        key = a.get("key")
        val = _scalar(a.get("value"))
        if isinstance(key, str) and val is not None:
            out[key] = val
    return out


def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _events(raw: Any) -> tuple[FlatSpanEvent, ...]:
    """OTLP span `events` -> FlatSpanEvent tuple. Total: non-list / malformed entries skipped."""
    if not isinstance(raw, list):
        return ()
    out: list[FlatSpanEvent] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        out.append(
            FlatSpanEvent(
                name=str(e.get("name", "")),
                time_ns=_to_int(e.get("timeUnixNano")),
                attrs=_flatten_attrs(e.get("attributes")),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class FlatLogRecord:
    """One OTLP LOG record, flattened for adapters. Some harnesses emit rich content on
    the logs signal — assistant responses, api bodies, tool decisions — rather than in spans.
    `event_name` is the record's `event.name` attr (fallback: the body). Agent-agnostic."""

    event_name: str
    time_ns: int
    attrs: Mapping[str, str]
    body: str
    raw_seq: int = 0


def flatten_logs(payload: Any, *, raw_seq: int = 0) -> list[FlatLogRecord]:
    """Raw OTLP logs payload ({"resourceLogs": [...]}) -> FlatLogRecord[]. Never raises."""
    out: list[FlatLogRecord] = []
    resource_logs = _dict(payload).get("resourceLogs")
    if not isinstance(resource_logs, list):
        return out
    for rl in resource_logs:
        if not isinstance(rl, dict):
            continue
        scope_logs = rl.get("scopeLogs")
        if not isinstance(scope_logs, list):
            continue
        for sl in scope_logs:
            if not isinstance(sl, dict):
                continue
            recs = sl.get("logRecords")
            if not isinstance(recs, list):
                continue
            for lr in recs:
                if not isinstance(lr, dict):
                    continue
                attrs = _flatten_attrs(lr.get("attributes"))
                body = str(_dict(lr.get("body")).get("stringValue", ""))
                out.append(
                    FlatLogRecord(
                        # Prefer the body: harnesses put the FULLY-QUALIFIED event name there
                        # (e.g. "<scope>.assistant_response"), while the `event.name` attr may be
                        # the bare suffix ("assistant_response"). Fall back to the attr.
                        event_name=body or attrs.get("event.name") or "",
                        # `a or b` on the RAW values is wrong: some exporters emit timeUnixNano="0"
                        # and put the real time only in observedTimeUnixNano — and the string "0" is
                        # truthy, so the fallback would never fire and every record would collapse
                        # to one ts. Parse first, then fall back when the send time is a genuine 0.
                        time_ns=_to_int(lr.get("timeUnixNano"))
                        or _to_int(lr.get("observedTimeUnixNano")),
                        attrs=attrs,
                        body=body,
                        raw_seq=raw_seq,
                    )
                )
    return out


def flatten(payload: Any, *, raw_seq: int = 0) -> list[FlatSpan]:
    """Raw OTLP payload ({"resourceSpans": [...]}) -> FlatSpan[]. Never raises."""
    spans: list[FlatSpan] = []
    resource_spans = _dict(payload).get("resourceSpans")
    if not isinstance(resource_spans, list):
        return spans
    for rs in resource_spans:
        if not isinstance(rs, dict):
            continue
        resource = _flatten_attrs(_dict(rs.get("resource")).get("attributes"))
        scope_spans = rs.get("scopeSpans")
        if not isinstance(scope_spans, list):
            continue
        for ss in scope_spans:
            if not isinstance(ss, dict):
                continue
            scope = str(_dict(ss.get("scope")).get("name", ""))
            sp_list = ss.get("spans")
            if not isinstance(sp_list, list):
                continue
            for sp in sp_list:
                if not isinstance(sp, dict):
                    continue
                status = _dict(sp.get("status"))
                spans.append(
                    FlatSpan(
                        span_id=str(sp.get("spanId", "")),
                        parent_span_id=str(sp.get("parentSpanId", "")),
                        trace_id=str(sp.get("traceId", "")),
                        name=str(sp.get("name", "")),
                        start_ns=_to_int(sp.get("startTimeUnixNano")),
                        end_ns=_to_int(sp.get("endTimeUnixNano")),
                        status_code=_to_int(status.get("code")),
                        attrs=_flatten_attrs(sp.get("attributes")),
                        scope=scope,
                        resource=resource,
                        raw_seq=raw_seq,
                        events=_events(sp.get("events")),
                    )
                )
    return spans
