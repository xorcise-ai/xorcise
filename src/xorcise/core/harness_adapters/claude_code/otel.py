# src/xorcise/core/otel/adapters/claude_code.py
"""ClaudeCodeAdapter — maps Anthropic Claude Code CLI OTel spans to AgentEvents.

Rides AgentTraceAdapter; self-registers at import; the core framework never imports it
(guard-tested). Built from a REAL captured trace (scope ``com.anthropic.claude_code.tracing``):

  claude_code.interaction         -> message(user) [user_prompt] AND the turn/group boundary
  claude_code.llm_request         -> DELEGATED to GenAiSemconvExtractor (gen_ai.*), then the
                                     metric is enriched with Claude Code's BESPOKE token attrs
                                     (input_tokens / output_tokens / cache_read_tokens /
                                     cache_creation_tokens) which are NOT gen_ai.usage.*
  claude_code.tool                -> file_edit | file_read | terminal_command (+output) | tool_call
                                     with the actual content/output from the tool.output span event
  claude_code.tool.execution      -> FOLDED onto the matching tool card as its status + duration
                                     (v4); a separate result event only for an orphan execution
  claude_code.tool.blocked_on_user-> status, but ONLY for a meaningful decision (reject/approve/…);
                                     a decision=unknown gate is auto-handled noise and is dropped
  claude_code.api_refusal (LOG)   -> error/model_refusal, with model + policy category metadata
  <anything else claude_code.*>   -> unknown (never crash)

DISPATCH ORDER MATTERS: claude_code.tool / tool.execution carry ``gen_ai.tool.call.id`` (a
gen_ai.* key), so GenAiSemconvExtractor.is_llm_span() is TRUE for them — we therefore match the
concrete claude_code.* span names FIRST and only fall back to is_llm_span() for anything else,
so tool spans are never misrouted into the LLM path.

CONTENT NOTE: tool OUTPUT — file content for Write/Edit/Read (``content``) and shell output for
Bash (``output``, with the command in ``bash_command``) — rides the ``tool.output`` span EVENT
(surfaced by flatten()), and this adapter maps it. What is STILL not
in the spans is the assistant's response FREE-TEXT: ``claude_code.llm_request`` carries only
tokens/model/stop_reason (no message body), so assistant prose and explicit refusal signals come
from OTLP *log records*. Pure + total: malformed/missing keys -> fewer events, never an exception.
"""

from __future__ import annotations

from datetime import UTC, datetime

from xorcise.core.contracts.agent_event import (
    AgentEvent,
    AgentEventKind,
    HarnessCapabilityProfile,
    RawTraceRef,
)
from xorcise.core.otel.adapters.base import AdapterContext, AgentTraceAdapter, profile_from
from xorcise.core.otel.adapters.genai import GenAiSemconvExtractor
from xorcise.core.otel.adapters.registry import register
from xorcise.core.otel.flatten import FlatLogRecord, FlatSpan

_INTERACTION = "claude_code.interaction"
_FILE_EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_FILE_READ_TOOLS = {"Read", "NotebookRead"}
_SHELL_TOOLS = {"Bash", "BashOutput", "KillShell"}
# Claude Code bespoke usage attrs (NOT gen_ai.usage.*) to graft onto the delegated metric.
_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")


class ClaudeCodeAdapter(AgentTraceAdapter):
    name = "claude-code"
    # v8: surface the explicit claude_code.api_refusal log as a model_refusal error.
    # v7: map the user_prompt LOG → user message (headless runs drop the interaction
    #   span that carried it), deduped against the span copy by the merge.
    # v6: _group_id falls back to the topmost ancestor when the interaction root was
    #   never exported (headless), so tool spans still group into turns instead of group_id=None.
    version = "8"
    _genai = GenAiSemconvExtractor()

    @property
    def capabilities(self) -> HarnessCapabilityProfile:
        return profile_from(
            self.name,
            self.version,
            supported=(
                AgentEventKind.message,
                AgentEventKind.terminal_command,
                AgentEventKind.terminal_output,
                AgentEventKind.file_edit,
                AgentEventKind.file_read,
                AgentEventKind.tool_call,
                AgentEventKind.tool_result,
                AgentEventKind.error,
                AgentEventKind.status,
                AgentEventKind.metric,
                AgentEventKind.unknown,
            ),
            message_roles={"user": "supported", "agent": "supported"},
            notes={
                "thinking": "Claude Code's OTel exporter does not emit thinking traces.",
                "mcp_call": "Claude Code reports MCP calls as generic tool calls, "
                "not distinct MCP events.",
            },
        )

    def normalize(self, spans: list[FlatSpan], ctx: AdapterContext) -> list[AgentEvent]:
        by_id = {s.span_id: s for s in spans if s.span_id}
        # tool_use_id -> tool_name from the claude_code.tool spans (used to fold/label the paired
        # execution result).
        tool_names = {
            str(s.attrs["tool_use_id"]): str(s.attrs["tool_name"])
            for s in spans
            if s.name == "claude_code.tool"
            and s.attrs.get("tool_use_id")
            and s.attrs.get("tool_name")
        }
        # tool_use_id -> (status, duration_ms) from the execution spans, folded onto the tool card
        # (v4) so there's no separate empty "<Tool> result" event.
        execs = {
            str(s.attrs["tool_use_id"]): (
                self._success_status(s),
                str(s.attrs.get("duration_ms") or ""),
            )
            for s in spans
            if s.name == "claude_code.tool.execution" and s.attrs.get("tool_use_id")
        }
        events: list[AgentEvent] = []
        for span in spans:
            events.extend(self._span_events(span, ctx, by_id, tool_names, execs))
        return events

    def normalize_logs(self, logs: list[FlatLogRecord], ctx: AdapterContext) -> list[AgentEvent]:
        """Map Claude Code's OTLP LOG events (scope com.anthropic.claude_code.events) to
        AgentEvents: explicit model refusals (`claude_code.api_refusal` → error), the assistant's
        response TEXT
        (`claude_code.assistant_response.response` → agent `message`) and the user's prompt
        (`claude_code.user_prompt.prompt` → user `message`). The user prompt normally rides the
        interaction span, but headless runs don't export it; the merge dedups the two sources by
        body so it never shows twice. Pure + total."""
        out: list[AgentEvent] = []
        for i, rec in enumerate(logs):
            if rec.event_name == "claude_code.api_refusal":
                model = str(rec.attrs.get("model") or "Claude")
                category = str(rec.attrs.get("category") or "")
                explanation = str(
                    rec.attrs.get("explanation")
                    or rec.attrs.get("reason")
                    or rec.attrs.get("message")
                    or ""
                )
                if explanation and explanation != "<REDACTED>":
                    body = explanation
                else:
                    body = f"{model} refused the request"
                    if category:
                        body += f" (category: {category})"
                    body += "."
                data_keys = (
                    "model",
                    "category",
                    "request_id",
                    "attempt",
                    "effort",
                    "query_source",
                    "has_explanation",
                )
                out.append(
                    AgentEvent(
                        run_id=ctx.run_id,
                        id=f"log:{rec.raw_seq}:{i}:refusal",
                        ts=self._log_ts(rec, ctx),
                        source_agent=ctx.source_agent,
                        kind=AgentEventKind.error,
                        subkind="model_refusal",
                        role="agent",
                        title="model refusal",
                        body=body,
                        data={k: rec.attrs[k] for k in data_keys if rec.attrs.get(k)},
                        status="error",
                        severity="error",
                        raw_ref=RawTraceRef(
                            run_id=ctx.run_id,
                            raw_seq=rec.raw_seq,
                            span_id="",
                            signal="log",
                        ),
                    )
                )
                continue
            # The user's prompt: normally the claude_code.interaction span carries it, but headless
            # runs never export that span, so the user_prompt LOG is the only source — map it (the
            # merge dedups against a span-provided copy so it never shows twice).
            if rec.event_name == "claude_code.user_prompt":
                prompt = str(rec.attrs.get("prompt") or "")
                if not prompt or prompt == "<REDACTED>":
                    continue
                data = {k: rec.attrs[k] for k in ("prompt.id",) if rec.attrs.get(k)}
                out.append(
                    AgentEvent(
                        run_id=ctx.run_id,
                        id=f"log:{rec.raw_seq}:{i}:user",
                        ts=self._log_ts(rec, ctx),
                        source_agent=ctx.source_agent,
                        kind=AgentEventKind.message,
                        role="user",
                        title="user message",
                        body=prompt,
                        data=data,
                        raw_ref=RawTraceRef(
                            run_id=ctx.run_id,
                            raw_seq=rec.raw_seq,
                            span_id="",
                            signal="log",
                        ),
                    )
                )
                continue
            if rec.event_name != "claude_code.assistant_response":
                continue
            text = str(rec.attrs.get("response") or "")
            if not text or text == "<REDACTED>":
                continue  # OTEL_LOG_ASSISTANT_RESPONSES off / redacted — nothing to render
            data = {
                k: rec.attrs[k]
                for k in ("model", "request_id", "response_length")
                if rec.attrs.get(k)
            }
            out.append(
                AgentEvent(
                    run_id=ctx.run_id,
                    id=f"log:{rec.raw_seq}:{i}:assistant",
                    ts=self._log_ts(rec, ctx),
                    source_agent=ctx.source_agent,
                    kind=AgentEventKind.message,
                    role="agent",  # the assistant speaking
                    title="assistant message",
                    body=text,
                    data=data,
                    raw_ref=RawTraceRef(
                        run_id=ctx.run_id,
                        raw_seq=rec.raw_seq,
                        span_id="",
                        signal="log",
                    ),
                )
            )
        return out

    def _log_ts(self, rec: FlatLogRecord, ctx: AdapterContext) -> datetime:
        if rec.time_ns > 0:
            try:
                return datetime.fromtimestamp(rec.time_ns / 1e9, tz=UTC)
            except (OverflowError, ValueError, OSError):
                pass
        created = ctx.created_at
        return created if created.tzinfo is not None else created.replace(tzinfo=UTC)

    def _group_id(self, span: FlatSpan, by_id: dict[str, FlatSpan]) -> str | None:
        """The turn key: the nearest claude_code.interaction ancestor's span_id (climb
        parent_span_id). Headless runs (`claude -p`) never export the interaction root, so its
        children reference a parent that isn't in the trace — then fall back to the TOPMOST
        ancestor reached (that unresolved parent id, or a non-interaction root). All spans under
        one interaction share it, so it still groups the turn; None only for a parentless span."""
        seen: set[str] = set()
        cur = span.parent_span_id
        top: str | None = None
        while cur and cur not in seen:
            seen.add(cur)
            top = cur
            anc = by_id.get(cur)
            if anc is None:
                return cur  # interaction span was never exported — its id is still the turn key
            if anc.name == _INTERACTION:
                return anc.span_id
            cur = anc.parent_span_id
        return top

    def _ts(self, span: FlatSpan, ctx: AdapterContext) -> datetime:
        if span.start_ns > 0:
            try:
                return datetime.fromtimestamp(span.start_ns / 1e9, tz=UTC)
            except (OverflowError, ValueError, OSError):
                pass
        created = ctx.created_at
        return created if created.tzinfo is not None else created.replace(tzinfo=UTC)

    def _ev(
        self,
        span: FlatSpan,
        ctx: AdapterContext,
        *,
        suffix: str,
        kind: AgentEventKind,
        title: str,
        role: str = "agent",
        body: str = "",
        status: str | None = None,
        severity: str = "info",
        data: dict[str, str] | None = None,
        group_id: str | None = None,
    ) -> AgentEvent:
        base = span.span_id or f"{span.raw_seq}:{span.name}:{span.start_ns}"
        return AgentEvent(
            run_id=ctx.run_id,
            id=f"{base}:{suffix}",
            ts=self._ts(span, ctx),
            source_agent=ctx.source_agent,
            kind=kind,
            role=role,  # type: ignore[arg-type]  # always a valid Literal role
            title=title,
            body=body,
            data=data or {},
            group_id=group_id,
            parent_id=span.parent_span_id or None,
            status=status,  # type: ignore[arg-type]  # always "ok"/"error"/None
            severity=severity,  # type: ignore[arg-type]  # always a valid Literal
            raw_ref=RawTraceRef(
                run_id=ctx.run_id,
                raw_seq=span.raw_seq,
                span_id=span.span_id,
                trace_id=span.trace_id or None,
            ),
        )

    def _span_events(
        self,
        span: FlatSpan,
        ctx: AdapterContext,
        by_id: dict[str, FlatSpan],
        tool_names: dict[str, str],
        execs: dict[str, tuple[str | None, str]],
    ) -> list[AgentEvent]:
        name = span.name
        gid = self._group_id(span, by_id)

        # 1) LLM span: delegate to the shared extractor, enrich the metric with bespoke tokens,
        #    remap group_id to the enclosing interaction. (Name-checked FIRST — see module docs.)
        if name == "claude_code.llm_request":
            out: list[AgentEvent] = []
            for e in self._genai.extract(span, ctx):
                e = e.model_copy(update={"group_id": gid or e.group_id})
                if e.kind == AgentEventKind.metric:
                    e = self._enrich_metric(e, span)
                out.append(e)
            return out

        # 2) interaction: the user message AND the turn/group boundary.
        if name == _INTERACTION:
            prompt = span.attrs.get("user_prompt")
            if isinstance(prompt, str) and prompt:
                return [
                    self._ev(
                        span,
                        ctx,
                        suffix="msg",
                        kind=AgentEventKind.message,
                        role="user",
                        title="user message",
                        body=prompt,
                        group_id=span.span_id or gid,
                    )
                ]
            return []

        # 3) tool invocation -> file_edit / file_read / terminal_command (+output) / tool_call.
        #    The `tool.output` span event (captured with OTEL_LOG_TOOL_CONTENT) carries the actual
        #    content written / command output — surface it, not just the path.
        if name == "claude_code.tool":
            tool = str(span.attrs.get("tool_name") or "tool")
            path = str(span.attrs.get("file_path") or "")
            tool_use_id = str(span.attrs.get("tool_use_id") or "")
            out_ev = next((ev for ev in span.events if ev.name == "tool.output"), None)
            out_attrs = out_ev.attrs if out_ev else {}
            data = {"tool.name": tool}
            if tool_use_id:
                data["tool_use.id"] = tool_use_id
            # Fold the paired execution's outcome onto THIS card (v4): a status badge + duration,
            # instead of a separate empty "<Tool> result" event.
            status, duration = execs.get(tool_use_id, (None, ""))
            if duration:
                data["duration_ms"] = duration
            severity = "error" if status == "error" else "info"
            if tool in _FILE_EDIT_TOOLS or tool in _FILE_READ_TOOLS:
                content = str(out_attrs.get("content") or "")  # file text (Write/Edit/Read)
                data["path"] = path
                kind = (
                    AgentEventKind.file_read
                    if tool in _FILE_READ_TOOLS
                    else AgentEventKind.file_edit
                )
                return [
                    self._ev(
                        span,
                        ctx,
                        suffix="tool",
                        kind=kind,
                        title=f"{tool} {path}".strip(),
                        body=content or path,  # written/read content if captured, else the path
                        status=status,
                        severity=severity,
                        data=data,
                        group_id=gid,
                    )
                ]
            if tool in _SHELL_TOOLS:
                # The command is the `full_command` span attr (fallback: the `bash_command` event
                # attr); the OUTPUT rides the `output` event key, NOT `content` (this is
                # what v2 missed, so shell cards showed the command but never its output).
                cmd = str(span.attrs.get("full_command") or out_attrs.get("bash_command") or "")
                output = str(out_attrs.get("output") or out_attrs.get("content") or "")
                evs = [
                    self._ev(
                        span,
                        ctx,
                        suffix="tool",
                        kind=AgentEventKind.terminal_command,
                        title="terminal",
                        body=cmd,
                        status=status,
                        severity=severity,
                        data=data,
                        group_id=gid,
                    )
                ]
                if output:
                    evs.append(
                        self._ev(
                            span,
                            ctx,
                            suffix="out",
                            kind=AgentEventKind.terminal_output,
                            role="tool",
                            title="output",
                            body=output,
                            group_id=gid,
                        )
                    )
                return evs
            # Any other tool (Skill / ToolSearch / TodoWrite / MCP …): output rides either key.
            content = str(out_attrs.get("content") or out_attrs.get("output") or "")
            evs = [
                self._ev(
                    span,
                    ctx,
                    suffix="tool",
                    kind=AgentEventKind.tool_call,
                    title=tool,
                    body="",
                    status=status,
                    severity=severity,
                    data=data,
                    group_id=gid,
                )
            ]
            if content:
                evs.append(
                    self._ev(
                        span,
                        ctx,
                        suffix="result",
                        kind=AgentEventKind.tool_result,
                        role="tool",
                        title="result",
                        body=content,
                        group_id=gid,
                    )
                )
            return evs

        # 4) tool execution result. Its outcome (status + duration) is FOLDED onto the matching
        #    claude_code.tool card (v4) — drop it when that card exists (the common case) rather
        #    than emit an empty result. Keep it only for an ORPHAN execution (no tool span).
        if name == "claude_code.tool.execution":
            tuid = str(span.attrs.get("tool_use_id") or "")
            if tuid in tool_names:
                return []  # folded onto the claude_code.tool event
            status = self._success_status(span)
            data = {}
            for k in ("tool_use_id", "duration_ms"):
                if span.attrs.get(k) is not None:
                    data[k] = str(span.attrs[k])
            return [
                self._ev(
                    span,
                    ctx,
                    suffix="exec",
                    kind=AgentEventKind.tool_result,
                    role="tool",
                    title="tool result",
                    status=status,
                    severity="error" if status == "error" else "info",
                    data=data,
                    group_id=gid,
                )
            ]

        # 5) permission gate -> status. Only MEANINGFUL decisions (reject / approve / deny …) — a
        #    `decision=unknown` gate is the auto-handled / no-prompt path (v4: dropped as noise; the
        #    tool's own card + status already show what happened).
        if name == "claude_code.tool.blocked_on_user":
            decision = str(span.attrs.get("decision") or "unknown")
            if decision == "unknown":
                return []
            return [
                self._ev(
                    span,
                    ctx,
                    suffix="perm",
                    kind=AgentEventKind.status,
                    title="permission gate",
                    body=f"decision={decision}",
                    data={"decision": decision, "source": str(span.attrs.get("source") or "")},
                    group_id=gid,
                )
            ]

        # 6) any other LLM-ish span (no claude_code.* match) -> shared extractor.
        if GenAiSemconvExtractor.is_llm_span(span):
            return [
                e.model_copy(update={"group_id": gid or e.group_id})
                for e in self._genai.extract(span, ctx)
            ]

        # 7) unseen claude_code.* span -> unknown (never crash).
        return [
            self._ev(
                span,
                ctx,
                suffix="unknown",
                kind=AgentEventKind.unknown,
                title=name or "span",
                body="",
                group_id=gid,
            )
        ]

    def _enrich_metric(self, e: AgentEvent, span: FlatSpan) -> AgentEvent:
        """Graft Claude Code's bespoke token attrs onto the delegated (model-only) metric."""
        extra = {k: str(span.attrs[k]) for k in _TOKEN_KEYS if span.attrs.get(k) is not None}
        stop = span.attrs.get("stop_reason")
        if isinstance(stop, str) and stop:
            extra["stop_reason"] = stop
        if not extra:
            return e
        bits = []
        if "input_tokens" in extra:
            bits.append(f"in={extra['input_tokens']}")
        if "output_tokens" in extra:
            bits.append(f"out={extra['output_tokens']}")
        return e.model_copy(update={"data": {**e.data, **extra}, "body": e.body or " ".join(bits)})

    @staticmethod
    def _success_status(span: FlatSpan) -> str | None:
        val = span.attrs.get("success")
        if val is None:
            return None
        return "ok" if str(val).lower() == "true" else "error"


register(ClaudeCodeAdapter())
