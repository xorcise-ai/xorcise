# src/xorcise/core/otel/adapters/openhands.py
"""OpenHandsAdapter — the FIRST concrete agent adapter, built ENTIRELY behind
AgentTraceAdapter. The core framework never imports it (guard-tested); it self-registers at
import. Maps OpenHands-SDK / Laminar (lmnr.tracer) spans to AgentEvents:

  conversation.send_message  -> message (role user)      [input.message; str OR {content:[…]}]
  agent.step                 -> group boundary (no event; its span_id groups descendants)
  _execute_action_event      -> thinking                 [input.action_event.reasoning_content]
  ThinkAction (TOOL)         -> thinking                 [input.action.thought]
  TaskTrackerAction (TOOL)   -> tool_call (plan card)     [input.action.command / task_list]
  TerminalAction (TOOL)      -> terminal_command + terminal_output  [input.action.command / output]
  FileEditorAction (TOOL)    -> file_edit | file_read     [input.action.command / path]
  litellm.completion / LLM   -> DELEGATED to GenAiSemconvExtractor. A tool_call is DROPPED when its
                                command matches a dedicated action span (TerminalAction/…) — that
                                span is canonical, so keeping the tool_call would duplicate it. But
                                an ORPHAN tool_call (a command with NO matching action span — e.g. a
                                high-risk action OpenHands runs without a TerminalAction span) is
                                SURFACED, so no real agent action is invisible. The
                                assistant text + usage metric are always kept (as Claude Code does).
                                Failed LLM spans also surface their OTel exception as one clean
                                error event; policy blocks are labelled as model refusals.
  <anything else>            -> unknown (never crash)

Pure + total: malformed lmnr blobs / missing keys yield fewer events, never an exception.
Imports stdlib + contracts.agent_event + otel.flatten + otel.adapters.{base,genai,registry}.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from xorcise.core.contracts.agent_event import (
    AgentEvent,
    AgentEventKind,
    HarnessCapabilityProfile,
    RawTraceRef,
)
from xorcise.core.otel.adapters.base import AdapterContext, AgentTraceAdapter, profile_from
from xorcise.core.otel.adapters.genai import GenAiSemconvExtractor
from xorcise.core.otel.adapters.registry import register
from xorcise.core.otel.flatten import FlatSpan


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _text_content(obj: Any) -> str:
    """OpenHands content: a str, or {"content":[{"type":"text","text":...}]} -> flat text."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        obj = obj.get("content")
    parts: list[str] = []
    if isinstance(obj, list):
        for p in obj:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
            elif isinstance(p, str):
                parts.append(p)
    return "\n".join(parts)


def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


class OpenHandsAdapter(AgentTraceAdapter):
    name = "openhands"
    # v2: surface failed LLM exceptions, including provider policy refusals, instead of leaving
    #     only an empty-body model metric in the replay.
    version = "2"
    _genai = GenAiSemconvExtractor()
    # Span names that ARE the canonical execution of a tool call (they carry `action.command`).
    _ACTION_SPANS = ("TerminalAction", "TaskTrackerAction", "FileEditorAction")

    @property
    def capabilities(self) -> HarnessCapabilityProfile:
        return profile_from(
            self.name,
            self.version,
            supported=(
                AgentEventKind.message,
                AgentEventKind.thinking,
                AgentEventKind.terminal_command,
                AgentEventKind.terminal_output,
                AgentEventKind.file_edit,
                AgentEventKind.file_read,
                AgentEventKind.tool_call,
                AgentEventKind.error,
                AgentEventKind.metric,
                AgentEventKind.unknown,
            ),
            message_roles={"user": "supported", "agent": "supported"},
            notes={
                "tool_result": "OpenHands reports tool calls without separate result events.",
            },
        )

    def normalize(self, spans: list[FlatSpan], ctx: AdapterContext) -> list[AgentEvent]:
        by_id = {s.span_id: s for s in spans if s.span_id}
        actioned = self._actioned_commands(spans)
        events: list[AgentEvent] = []
        for span in spans:
            events.extend(self._span_events(span, ctx, by_id, actioned))
        return events

    def _actioned_commands(self, spans: list[FlatSpan]) -> set[str]:
        """The commands OpenHands ran via a dedicated action span. An LLM tool_call whose command
        is in here is a duplicate of that span (dropped); one that is NOT is an orphan the LLM
        turn is the only record of (surfaced) — see the module docstring."""
        cmds: set[str] = set()
        for s in spans:
            if s.name in self._ACTION_SPANS:
                action = _dict(_dict(_loads(s.attrs.get("lmnr.span.input"))).get("action"))
                cmd = action.get("command")
                if isinstance(cmd, str) and cmd:
                    cmds.add(cmd)
        return cmds

    @staticmethod
    def _is_orphan_tool_call(event: AgentEvent, actioned: set[str]) -> bool:
        """True for a tool_call whose command has no matching action span — surface it. A tool_call
        we cannot pin to a command (no `command` in its args) is NOT treated as an orphan, so the
        conservative default stays 'drop' (unchanged for such calls)."""
        args = _loads(event.body)
        cmd = args.get("command") if isinstance(args, dict) else None
        return isinstance(cmd, str) and bool(cmd) and cmd not in actioned

    def _group_id(self, span: FlatSpan, by_id: dict[str, FlatSpan]) -> str | None:
        """The nearest agent.step ancestor's span_id (climb parent_span_id)."""
        seen: set[str] = set()
        cur = span.parent_span_id
        while cur and cur in by_id and cur not in seen:
            seen.add(cur)
            anc = by_id[cur]
            if anc.name == "agent.step":
                return anc.span_id
            cur = anc.parent_span_id
        return None

    @staticmethod
    def _llm_error_details(span: FlatSpan) -> tuple[str, str, dict[str, str]] | None:
        """Extract one display-safe failure from repeated Laminar exception events."""
        if span.status_code != 2:
            return None
        raw = ""
        exception_type = ""
        for event in span.events:
            if event.name != "exception":
                continue
            raw = event.attrs.get("exception.message") or raw
            exception_type = event.attrs.get("exception.type") or exception_type
        if not raw:
            return None

        # LiteLLM prefixes the provider JSON with its exception class. Prefer the provider's
        # concise error message over exposing that wrapper (or its stack trace) in the replay.
        message = raw.strip()
        error_code = ""
        error_type = ""
        brace = message.find("{")
        if brace >= 0:
            parsed = _loads(message[brace:])
            error = _dict(_dict(parsed).get("error"))
            provider_message = error.get("message")
            if isinstance(provider_message, str) and provider_message.strip():
                message = provider_message.strip()
            error_code = str(error.get("code") or "")
            error_type = str(error.get("type") or "")

        refusal_markers = (
            "cyber_policy",
            "content_filter",
            "content_policy",
            "policy_violation",
            "moderation",
            "safety",
        )
        refusal_text = f"{error_code} {error_type} {message}".lower()
        is_refusal = any(marker in refusal_text for marker in refusal_markers) or (
            "content was flagged" in refusal_text
        )
        data = {"body": message}
        if error_code:
            data["error.code"] = error_code
        if error_type:
            data["error.type"] = error_type
        if exception_type:
            data["exception.type"] = exception_type
        return (
            "model refusal" if is_refusal else "model request failed",
            "model_refusal" if is_refusal else "model_error",
            data,
        )

    def _llm_error_event(
        self, span: FlatSpan, ctx: AdapterContext, group_id: str | None
    ) -> AgentEvent | None:
        details = self._llm_error_details(span)
        if details is None:
            return None
        title, subkind, details_data = details
        body = details_data.pop("body")
        base = span.span_id or f"{span.raw_seq}:{span.name}:{span.start_ns}"
        return AgentEvent(
            run_id=ctx.run_id,
            id=f"{base}:error",
            ts=self._ts(span, ctx),
            source_agent=ctx.source_agent,
            kind=AgentEventKind.error,
            subkind=subkind,
            role="agent",
            title=title,
            body=body,
            data=details_data,
            group_id=group_id,
            parent_id=span.parent_span_id or None,
            status="error",
            severity="error",
            raw_ref=RawTraceRef(
                run_id=ctx.run_id,
                raw_seq=span.raw_seq,
                span_id=span.span_id,
                trace_id=span.trace_id or None,
            ),
        )

    def _ts(self, span: FlatSpan, ctx: AdapterContext) -> datetime:
        if span.start_ns > 0:
            try:
                return datetime.fromtimestamp(span.start_ns / 1e9, tz=UTC)
            except (OverflowError, ValueError, OSError):
                pass
        created = ctx.created_at
        return created if created.tzinfo is not None else created.replace(tzinfo=UTC)

    def _span_events(
        self,
        span: FlatSpan,
        ctx: AdapterContext,
        by_id: dict[str, FlatSpan],
        actioned: set[str],
    ) -> list[AgentEvent]:
        gid = self._group_id(span, by_id)
        name = span.name

        # LLM spans: delegate to the shared extractor; remap group_id to the enclosing step. Drop a
        # tool_call only when a dedicated action span (TerminalAction/…) already renders its command
        # (avoid the duplicate); SURFACE an orphan tool_call whose command has no action span, so a
        # high-security-risk action OpenHands ran without one stays visible.
        if name == "litellm.completion" or GenAiSemconvExtractor.is_llm_span(span):
            kept: list[AgentEvent] = []
            for e in self._genai.extract(span, ctx):
                is_tc = e.kind is AgentEventKind.tool_call
                if is_tc and not self._is_orphan_tool_call(e, actioned):
                    continue  # duplicates a dedicated action span → the action layer is canonical
                kept.append(e.model_copy(update={"group_id": gid or e.group_id}))
            failure = self._llm_error_event(span, ctx, gid)
            if failure is not None:
                kept.append(failure)
            return kept

        # agent.step is a structural group boundary — no event.
        if name == "agent.step":
            return []

        ts = self._ts(span, ctx)
        base = span.span_id or f"{span.raw_seq}:{name}:{span.start_ns}"
        ref = RawTraceRef(
            run_id=ctx.run_id,
            raw_seq=span.raw_seq,
            span_id=span.span_id,
            trace_id=span.trace_id or None,
        )

        def ev(
            *,
            suffix: str,
            kind: AgentEventKind,
            title: str,
            role: str = "agent",
            body: str = "",
            status: str | None = None,
            severity: str = "info",
            data: dict[str, str] | None = None,
        ) -> AgentEvent:
            return AgentEvent(
                run_id=ctx.run_id,
                id=f"{base}:{suffix}",
                ts=ts,
                source_agent=ctx.source_agent,
                kind=kind,
                role=role,  # type: ignore[arg-type]  # value is always one of the Literal roles
                title=title,
                body=body,
                data=data or {},
                group_id=gid,
                parent_id=span.parent_span_id or None,
                status=status,  # type: ignore[arg-type]  # value is always "ok"/"error"/None
                severity=severity,  # type: ignore[arg-type]  # value is always a valid Literal
                raw_ref=ref,
            )

        if name == "conversation.send_message":
            # The mission rides `message`, which is EITHER a plain string (SDK path) OR a message
            # dict {"role":"user","content":[{"type":"text","text":…}]} (headless `--headless -t`).
            # _text_content flattens both — str-only handling silently dropped the headless prompt.
            msg = _text_content(_dict(_loads(span.attrs.get("lmnr.span.input"))).get("message"))
            if msg:
                return [
                    ev(
                        suffix="msg",
                        kind=AgentEventKind.message,
                        role="user",
                        title="user message",
                        body=msg,
                    )
                ]
            return []

        if name == "ThinkAction":
            action = _dict(_dict(_loads(span.attrs.get("lmnr.span.input"))).get("action"))
            thought = action.get("thought")
            if isinstance(thought, str) and thought.strip():
                return [
                    ev(
                        suffix="think",
                        kind=AgentEventKind.thinking,
                        title="thinking",
                        body=thought,
                    )
                ]
            return []

        if name == "TaskTrackerAction":
            action = _dict(_dict(_loads(span.attrs.get("lmnr.span.input"))).get("action"))
            cmd = str(action.get("command") or "")
            tasks = action.get("task_list")
            lines: list[str] = []
            if isinstance(tasks, list):
                for t in tasks:
                    if isinstance(t, dict):
                        lines.append(f"[{t.get('status', '?')}] {t.get('title', '')}".rstrip())
            return [
                ev(
                    suffix="task",
                    kind=AgentEventKind.tool_call,
                    title=f"task_tracker {cmd}".strip(),
                    body="\n".join(lines),
                    data={"command": cmd} if cmd else None,
                )
            ]

        if name == "_execute_action_event":
            ae = _dict(_dict(_loads(span.attrs.get("lmnr.span.input"))).get("action_event"))
            reasoning = ae.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                return [
                    ev(
                        suffix="think",
                        kind=AgentEventKind.thinking,
                        title="thinking",
                        body=reasoning,
                    )
                ]
            return []

        if name == "TerminalAction":
            inp = _dict(_loads(span.attrs.get("lmnr.span.input")))
            action = _dict(inp.get("action"))
            command = action.get("command")
            out = _loads(span.attrs.get("lmnr.span.output"))
            output_text = _text_content(out)
            is_err = bool(_dict(out).get("is_error"))
            events: list[AgentEvent] = []
            if isinstance(command, str) and command:
                events.append(
                    ev(
                        suffix="cmd",
                        kind=AgentEventKind.terminal_command,
                        title="terminal",
                        body=command,
                    )
                )
            if output_text:
                events.append(
                    ev(
                        suffix="out",
                        kind=AgentEventKind.terminal_output,
                        role="tool",
                        title="output",
                        body=output_text,
                        status="error" if is_err else "ok",
                        severity="error" if is_err else "info",
                    )
                )
            return events or [
                ev(suffix="cmd", kind=AgentEventKind.terminal_command, title="terminal", body="")
            ]

        if name == "FileEditorAction":
            inp = _dict(_loads(span.attrs.get("lmnr.span.input")))
            action = _dict(inp.get("action"))
            command = str(action.get("command") or "")
            path = str(action.get("path") or "")
            is_err = bool(_dict(_loads(span.attrs.get("lmnr.span.output"))).get("is_error"))
            kind = AgentEventKind.file_read if command == "view" else AgentEventKind.file_edit
            return [
                ev(
                    suffix="file",
                    kind=kind,
                    title=(f"{command} {path}".strip() or "file"),
                    body=path,
                    status="error" if is_err else None,
                    data={"path": path, "op": command},
                )
            ]

        # Unseen OpenHands span -> unknown (never crash).
        return [ev(suffix="unknown", kind=AgentEventKind.unknown, title=name or "span", body="")]


register(OpenHandsAdapter())
