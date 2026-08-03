# src/xorcise/core/otel/adapters/genai.py
"""GenAiSemconvExtractor — shared AI/LLM layer.

Maps a standard OTel ``gen_ai.*`` semantic-convention LLM span to AgentEvents. Reusable by
ANY adapter whose LLM layer emits ``gen_ai.*`` (litellm / openllmetry / lmnr / OpenInference /
etc.) — no framework coupling, no framework-specific ``subkind``. This is the shared LLM layer
of the adapter framework: OpenHandsAdapter and every later adapter (Claude Code,
LangGraph, CrewAI, AutoGen) delegate their LLM spans here instead of re-implementing gen_ai
parsing. It is a standalone helper — NOT wired into normalize_run/registry/generic here.

Pure + defensive: a span missing usage / messages, or carrying malformed JSON, yields fewer
events, never an exception. Imports stdlib + contracts.agent_event + otel.flatten +
otel.adapters.base only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from xorcise.core.contracts.agent_event import AgentEvent, AgentEventKind, RawTraceRef
from xorcise.core.otel.adapters.base import AdapterContext
from xorcise.core.otel.flatten import FlatSpan

# Parsed message role -> AgentEvent.role (contract Literal). Unknown -> "agent".
_ROLE_MAP: dict[str, str] = {
    "assistant": "agent",
    "user": "user",
    "system": "system",
    "tool": "tool",
}

# gen_ai.* / llm.* usage attributes surfaced on the metric event (short name -> value).
_USAGE_KEYS = (
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.cache_read_input_tokens",
    "gen_ai.usage.cache_creation_input_tokens",
    "gen_ai.usage.total_tokens",
    "llm.usage.total_tokens",
)


def _span_ts(span: FlatSpan, ctx: AdapterContext) -> datetime:
    """span.start_ns -> tz-aware UTC datetime; fall back to a tz-aware ctx.created_at.

    A pathological start_ns (a malformed startTimeUnixNano exceeding datetime's range —
    flatten._to_int does not clamp to int64) falls back rather than raising: this shared
    helper is called for every LLM span by every adapter and must never break replay.
    """
    if span.start_ns > 0:
        try:
            return datetime.fromtimestamp(span.start_ns / 1e9, tz=UTC)
        except (OverflowError, ValueError, OSError):
            pass
    created = ctx.created_at
    return created if created.tzinfo is not None else created.replace(tzinfo=UTC)


def _loads(raw: str | None) -> Any:
    """Parse a JSON attribute blob; malformed/missing -> None (never raises)."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _text_of(content: Any) -> str:
    """gen_ai message content: a plain string OR a list of {type,text} parts -> flat text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
            elif isinstance(p, str):
                parts.append(p)
        return "\n".join(parts)
    return ""


class GenAiSemconvExtractor:
    """Extract standard OTel ``gen_ai.*`` LLM info from a FlatSpan. Reusable across adapters."""

    version = "1"

    @staticmethod
    def is_llm_span(span: FlatSpan) -> bool:
        """True when the span carries gen_ai.* semconv OR is tagged ``lmnr.span.type == "LLM"``."""
        if span.attrs.get("lmnr.span.type") == "LLM":
            return True
        return any(k.startswith("gen_ai.") for k in span.attrs)

    def extract(self, span: FlatSpan, ctx: AdapterContext) -> list[AgentEvent]:
        """gen_ai.* LLM span -> assistant message(s) + tool_call(s) + a usage/model metric.

        Grouping is left framework-neutral: events from one LLM call share
        ``group_id = span.span_id``. An adapter (e.g. OpenHandsAdapter) may remap group_id to
        an enclosing step. Missing usage/messages are tolerated (fewer events, never a raise).
        """
        events: list[AgentEvent] = []
        ts = _span_ts(span, ctx)
        base_id = span.span_id or f"{span.raw_seq}:{span.name}:{span.start_ns}"
        group_id = span.span_id or None
        parent_id = span.parent_span_id or None
        raw_ref = RawTraceRef(
            run_id=ctx.run_id,
            raw_seq=span.raw_seq,
            span_id=span.span_id,
            trace_id=span.trace_id or None,
        )

        def _event(
            *,
            id: str,
            kind: AgentEventKind,
            title: str,
            role: str = "agent",
            body: str = "",
            data: dict[str, str] | None = None,
        ) -> AgentEvent:
            return AgentEvent(
                run_id=ctx.run_id,
                id=id,
                ts=ts,
                source_agent=ctx.source_agent,
                kind=kind,
                role=role,  # type: ignore[arg-type]  # value comes from _ROLE_MAP (a valid Literal)
                title=title,
                body=body,
                data=data or {},
                group_id=group_id,
                parent_id=parent_id,
                raw_ref=raw_ref,
            )

        # 1) assistant message(s) + their tool_calls, from gen_ai.output.messages
        messages = _loads(span.attrs.get("gen_ai.output.messages"))
        if isinstance(messages, list):
            for m_idx, msg in enumerate(messages):
                if not isinstance(msg, dict):
                    continue
                role = _ROLE_MAP.get(str(msg.get("role", "assistant")), "agent")
                text = _text_of(msg.get("content"))
                if text:
                    events.append(
                        _event(
                            id=f"{base_id}:msg:{m_idx}",
                            kind=AgentEventKind.message,
                            role=role,
                            title="assistant message" if role == "agent" else f"{role} message",
                            body=text,
                        )
                    )
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list):
                    for t_idx, tc in enumerate(tool_calls):
                        if not isinstance(tc, dict):
                            continue
                        fn = tc.get("function")
                        fn = fn if isinstance(fn, dict) else {}
                        name = str(fn.get("name") or tc.get("name") or "tool")
                        arguments = fn.get("arguments")
                        if isinstance(arguments, str):
                            args_str = arguments
                        elif arguments is not None:
                            args_str = json.dumps(arguments, separators=(",", ":"))
                        else:
                            args_str = ""
                        data = {"tool.name": name}
                        tc_id = tc.get("id")
                        if isinstance(tc_id, str) and tc_id:
                            data["tool_call.id"] = tc_id
                        events.append(
                            _event(
                                id=f"{base_id}:msg:{m_idx}:tool:{t_idx}",
                                kind=AgentEventKind.tool_call,
                                title=name,
                                body=args_str,
                                data=data,
                            )
                        )

        # 2) usage / model metric
        model = span.attrs.get("gen_ai.request.model") or span.attrs.get("gen_ai.response.model")
        usage = {k.rsplit(".", 1)[-1]: span.attrs[k] for k in _USAGE_KEYS if k in span.attrs}
        if model or usage:
            data = dict(usage)
            if model:
                data["model"] = model
            bits: list[str] = []
            if "input_tokens" in usage:
                bits.append(f"in={usage['input_tokens']}")
            if "output_tokens" in usage:
                bits.append(f"out={usage['output_tokens']}")
            events.append(
                _event(
                    id=f"{base_id}:metric",
                    kind=AgentEventKind.metric,
                    title=model or "llm usage",
                    body=" ".join(bits),
                    data=data,
                )
            )
        return events
