"""GenericOtelAdapter: the dumb, parity fallback adapter.

A faithful Python port of the frontend's `parse-trace.ts` `classify()`/`pickBody()` —
same regexes, same first-match-wins order — retargeted to build `AgentEvent`s instead of
`TraceEvent`s. This is deliberately NOT clever: no gen_ai-aware parsing, no span grouping.
It is the last resort `registry.select()` falls back to when no framework-specific adapter
is registered for a `source_agent`/resource/fingerprint. Smart mappings live in the
per-harness adapters.

Imports stdlib + xorcise.core.contracts.agent_event + xorcise.core.otel.flatten +
xorcise.core.otel.adapters.base only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha1

from xorcise.core.contracts.agent_event import (
    AgentEvent,
    AgentEventKind,
    HarnessCapabilityProfile,
    RawTraceRef,
)
from xorcise.core.otel.adapters.base import AdapterContext, AgentTraceAdapter, profile_from
from xorcise.core.otel.flatten import FlatSpan

# Same regexes, same order, as parse-trace.ts `classify()` — port 1:1, don't get clever.
_ERROR_RE = re.compile(r"error|fail|exception")
_FLAG_RE = re.compile(r"flag")
_MCP_RE = re.compile(r"mcp")
_TERMINAL_RE = re.compile(r"shell|exec|command|bash|terminal|cmd")
_THINKING_RE = re.compile(r"think|reason")
_TOOL_RE = re.compile(r"tool|read|write|glob|grep|edit|fetch|search")
_MESSAGE_RE = re.compile(r"assistant|message|llm|completion|response|model")

# Same key order as parse-trace.ts `pickBody()`.
_BODY_KEYS = ("command", "cmd", "input", "flag", "path", "query", "url")


def classify(name: str, attrs: Mapping[str, str], status_code: int) -> AgentEventKind:
    """Port of parse-trace.ts `classify()`. First-match-wins on the lower-cased span name."""
    n = name.lower()
    if status_code == 2 or _ERROR_RE.search(n):
        return AgentEventKind.error
    if _FLAG_RE.search(n):
        return AgentEventKind.flag
    if _MCP_RE.search(n) or "mcp.tool" in attrs:
        return AgentEventKind.mcp_call
    if _TERMINAL_RE.search(n):
        return AgentEventKind.terminal_command
    if _THINKING_RE.search(n):
        return AgentEventKind.thinking
    if _TOOL_RE.search(n):
        return AgentEventKind.tool_call
    if _MESSAGE_RE.search(n):
        return AgentEventKind.message
    return AgentEventKind.tool_call


def pick_body(attrs: Mapping[str, str]) -> str:
    """Port of parse-trace.ts `pickBody()`: first known key, else the first `k: v`, else ""."""
    for key in _BODY_KEYS:
        value = attrs.get(key)
        if value:
            return value
    for key, value in attrs.items():
        return f"{key}: {value}"
    return ""


class GenericOtelAdapter(AgentTraceAdapter):
    """Agent-agnostic, dumb parity fallback. Never the smart choice, always a safe one."""

    name = "generic"
    version = "1"

    @property
    def capabilities(self) -> HarnessCapabilityProfile:
        return profile_from(
            self.name,
            self.version,
            verified=False,
            supported=(
                AgentEventKind.message,
                AgentEventKind.thinking,
                AgentEventKind.terminal_command,
                AgentEventKind.tool_call,
                AgentEventKind.mcp_call,
                AgentEventKind.flag,
                AgentEventKind.error,
            ),
        )

    def normalize(self, spans: list[FlatSpan], ctx: AdapterContext) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        for span in spans:
            kind = classify(span.name, span.attrs, span.status_code)
            span_id = (
                span.span_id
                or sha1(f"{span.raw_seq}:{span.name}:{span.start_ns}".encode()).hexdigest()[:16]
            )
            if span.start_ns > 0:
                ts = datetime.fromtimestamp(span.start_ns / 1e9, tz=UTC)
            else:
                created = ctx.created_at
                ts = created if created.tzinfo is not None else created.replace(tzinfo=UTC)
            # Span duration (ms) when the span carries a real end — drives the
            # timeline waterfall's bar widths. None when the end is missing.
            duration_ms = (
                (span.end_ns - span.start_ns) // 1_000_000
                if span.end_ns > span.start_ns > 0
                else None
            )
            is_error = kind == AgentEventKind.error
            events.append(
                AgentEvent(
                    run_id=ctx.run_id,
                    id=span_id,
                    ts=ts,
                    duration_ms=duration_ms,
                    source_agent=ctx.source_agent,
                    kind=kind,
                    title=span.name or "span",
                    body=pick_body(span.attrs),
                    data=dict(span.attrs),
                    role="agent",
                    group_id=None,
                    parent_id=span.parent_span_id or None,
                    severity="error" if is_error else "info",
                    status="error" if is_error else None,
                    raw_ref=RawTraceRef(
                        run_id=ctx.run_id,
                        raw_seq=span.raw_seq,
                        span_id=span.span_id,
                        trace_id=span.trace_id or None,
                    ),
                )
            )
        return events
