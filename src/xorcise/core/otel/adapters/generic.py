"""GenericOtelAdapter: the dumb, honest fallback adapter.

Originally a 1:1 port of the frontend's `parse-trace.ts` `classify()`/`pickBody()`; that file
is gone, so this module is now the only implementation and may evolve. It is deliberately NOT
clever: keyword classification on the span name, first-match-wins, no gen_ai-aware parsing, no
span grouping. It is the last resort `registry.select()` falls back to when no
framework-specific adapter is registered for a `source_agent`/resource/fingerprint. Smart
mappings live in the per-harness adapters.

Honesty rule: a span whose name matches no keyword is `unclassified`, never `tool_call`, and
its body is only ever a known content key (command/cmd/input/…) — the adapter does not
fabricate a body out of the first attribute it finds. A harness that emits marker-only spans
(class + id + source, no payload) therefore renders as exactly that, with a warning from
`normalize_run`, instead of as a wall of confident-looking tool calls.

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

# Keyword classification, first-match-wins (inherited from the retired parse-trace.ts).
_ERROR_RE = re.compile(r"error|fail|exception")
_FLAG_RE = re.compile(r"flag")
_MCP_RE = re.compile(r"mcp")
_TERMINAL_RE = re.compile(r"shell|exec|command|bash|terminal|cmd")
_THINKING_RE = re.compile(r"think|reason")
_TOOL_RE = re.compile(r"tool|read|write|glob|grep|edit|fetch|search")
_MESSAGE_RE = re.compile(r"assistant|message|llm|completion|response|model")

# Content-bearing keys, in preference order. ONLY these become a body (see the honesty rule).
_BODY_KEYS = ("command", "cmd", "input", "flag", "path", "query", "url")


def classify(name: str, attrs: Mapping[str, str], status_code: int) -> AgentEventKind:
    """First-match-wins keyword classification on the lower-cased span name.

    Anything that matches no rule is `unclassified` — an honest label the UI shows by default —
    rather than a guessed `tool_call`."""
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
    return AgentEventKind.unclassified


def pick_body(attrs: Mapping[str, str]) -> str:
    """The first known content key's value, else "" — never a fabricated `k: v` of some other
    attribute (the attributes stay visible, unaltered, in `data`)."""
    for key in _BODY_KEYS:
        value = attrs.get(key)
        if value:
            return value
    return ""


class GenericOtelAdapter(AgentTraceAdapter):
    """Agent-agnostic, dumb parity fallback. Never the smart choice, always a safe one."""

    name = "generic"
    # v2: unmatched spans are `unclassified` (was `tool_call`) and the body is never fabricated
    #     from an arbitrary attribute. Bumping the version rebuilds every cached projection.
    version = "2"

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
                AgentEventKind.unclassified,
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
