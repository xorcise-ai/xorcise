"""CodexAdapter — maps OpenAI Codex CLI OTLP to AgentEvents.

Rides AgentTraceAdapter; self-registers at import; the core framework never imports it
(guard-tested). Built from a REAL codex-cli 0.145.0 run (fixtures/otlp/codex_real_run.json).

WHERE CODEX PUTS THINGS (verified against the capture): codex emits every event on BOTH signals —
a TRACE-SAFE copy on span *events* (``codex_otel.trace_safe``: metadata only — prompt_length,
arguments_length, output_length, tool_name, durations; NO text) and a LOG-ONLY copy as OTLP log
records (``codex_otel.log_only``: the FULL content — the prompt text, the shell command, the
command output). So the agent narrative is reconstructed from the LOGS (``normalize_logs``), and
the ~440 codex_core runtime spans (append_items / auth / handle_responses / …) plus their
trace-safe metadata duplicates are SUPPRESSED (``normalize`` returns []) rather than flooding the
replay with hundreds of contentless events.

  codex.user_prompt      -> message(user)                    [the task text]
  codex.tool_result      -> exec_command -> terminal_command (+ terminal_output)
                            apply_patch/write -> file_edit
                            mcp tool         -> mcp_call (+ mcp_result)
                            other            -> tool_call (+ tool_result)   [args + output]
  codex.tool_decision    -> status, ONLY for a non-approved decision (an approved one is the
                            auto-run path — dropped as noise; the tool card already shows it)
  codex.conversation_starts -> status (session header: model + reasoning effort)
  codex.sse_event(response.completed) -> metric (token usage); other sse frames dropped
  codex.turn_ttft        -> metric (time to first token)
  codex.api_request / websocket_* -> error status when they FAIL; dropped when successful (noise)
  codex.startup_phase    -> dropped (internal)
  <unhandled codex.* log>   -> unknown (so a new content event can't vanish silently)

CONTENT LIMITATION (documented): codex's OTel carries NO assistant response TEXT or reasoning
content — only token COUNTS (``codex.sse_event``). Unlike Claude Code (which has
OTEL_LOG_ASSISTANT_RESPONSES), codex has no flag to log the model's prose; that text lives only in
codex's own rollout transcript
(``~/.codex/sessions/**/rollout-*.jsonl``), not its telemetry. So the replay shows the full ACTION
narrative (prompt, tools, commands, outputs) but not the assistant's free-text answer.

Pure + total: malformed/missing keys or bad JSON -> fewer events, never an exception.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from xorcise.core.contracts.agent_event import (
    AgentEvent,
    AgentEventKind,
    HarnessCapabilityProfile,
    RawTraceRef,
)
from xorcise.core.otel.adapters.base import AdapterContext, AgentTraceAdapter, profile_from
from xorcise.core.otel.adapters.registry import register
from xorcise.core.otel.flatten import FlatLogRecord, FlatSpan

# codex's shell tool (runs a command); its output rides tool_result.output.
_SHELL_TOOLS = {
    "exec_command",
    "write_stdin",
    "wait",
    "shell",
    "local_shell",
    "container.exec",
}
# file-mutating tools; the patch/content rides tool_result.arguments.
_FILE_EDIT_TOOLS = {"apply_patch", "write_file", "edit_file", "str_replace_editor"}
# codex.* LOG events that are pure runtime internals with no useful signal — always dropped.
_NOISE_EVENTS = {"codex.startup_phase"}
# transport events: dropped when successful (noise), surfaced as an error status when they FAIL.
_TRANSPORT_EVENTS = {"codex.api_request", "codex.websocket_connect", "codex.websocket_request"}
# codex redacts the user prompt with SQUARE brackets (NOT claude_code's angle-bracket form) when
# otel.log_user_prompt is false — treat it as "no content".
_REDACTED = "[REDACTED]"
# decisions that mean "ran without a prompt" — the tool card already shows the action, so drop them.
_AUTO_DECISIONS = {"approved", "approve", "auto", "allow", "allowed"}


class CodexAdapter(AgentTraceAdapter):
    name = "codex"
    version = "1"

    @property
    def capabilities(self) -> HarnessCapabilityProfile:
        return profile_from(
            self.name,
            self.version,
            supported=(
                AgentEventKind.terminal_command,
                AgentEventKind.terminal_output,
                AgentEventKind.file_edit,
                AgentEventKind.tool_call,
                AgentEventKind.tool_result,
                AgentEventKind.mcp_call,
                AgentEventKind.mcp_result,
                AgentEventKind.status,
                AgentEventKind.metric,
                AgentEventKind.unknown,
            ),
            partial=(AgentEventKind.message,),
            message_roles={
                "user": "supported",
                "agent": "unsupported",
            },
            notes={
                "message": "User prompts only — Codex CLI does not export "
                "agent-authored chat messages.",
                "thinking": "Codex CLI does not export thinking traces.",
            },
        )

    def normalize(self, spans: list[FlatSpan], ctx: AdapterContext) -> list[AgentEvent]:
        """Suppress spans: codex's spans are codex_core runtime internals and their trace-safe
        metadata duplicates of the log events (no content). The narrative is built in
        normalize_logs from the log-only signal. Returning [] is deliberate — it's what keeps the
        replay clean instead of the ~440 contentless span events the generic projection emits."""
        return []

    def normalize_logs(self, logs: list[FlatLogRecord], ctx: AdapterContext) -> list[AgentEvent]:
        out: list[AgentEvent] = []
        for i, rec in enumerate(logs):
            out.extend(self._log_events(rec, i, ctx))
        return out

    def _log_events(self, rec: FlatLogRecord, i: int, ctx: AdapterContext) -> list[AgentEvent]:
        name = rec.event_name
        if name == "codex.user_prompt":
            prompt = str(rec.attrs.get("prompt") or "")
            if not prompt or prompt == _REDACTED:
                return []
            return [
                self._ev(
                    rec,
                    i,
                    ctx,
                    suffix="user",
                    kind=AgentEventKind.message,
                    role="user",
                    title="user message",
                    body=prompt,
                )
            ]
        if name == "codex.tool_result":
            return self._tool_result_events(rec, i, ctx)
        if name == "codex.tool_decision":
            decision = str(rec.attrs.get("decision") or "").strip()
            if not decision or decision.lower() in _AUTO_DECISIONS:
                return []  # auto-run path — the tool card shows the action; not narrative
            tool = str(rec.attrs.get("tool_name") or "tool")
            call_id = str(rec.attrs.get("call_id") or "")
            return [
                self._ev(
                    rec,
                    i,
                    ctx,
                    suffix="decision",
                    kind=AgentEventKind.status,
                    title="tool decision",
                    body=f"{tool}: {decision}",
                    severity="warning",
                    data={"tool.name": tool, "decision": decision},
                    # group with the tool card it gates (same call_id), so a denial visually links.
                    group_id=call_id or None,
                )
            ]
        if name == "codex.conversation_starts":
            model = str(rec.attrs.get("model") or rec.attrs.get("slug") or "")
            effort = str(rec.attrs.get("reasoning_effort") or "")
            bits = []
            if model:
                bits.append(f"model={model}")
            if effort:
                bits.append(f"effort={effort}")
            keys = ("model", "reasoning_effort", "provider_name")
            return [
                self._ev(
                    rec,
                    i,
                    ctx,
                    suffix="start",
                    kind=AgentEventKind.status,
                    role="system",
                    title="session started",
                    body=" ".join(bits),
                    data={k: str(rec.attrs[k]) for k in keys if rec.attrs.get(k)},
                )
            ]
        if name == "codex.sse_event":
            # only the terminal frame carries usage — earlier streaming frames are noise
            if str(rec.attrs.get("event.kind") or "") != "response.completed":
                return []
            token_keys = (
                "input_token_count",
                "output_token_count",
                "cached_token_count",
                "reasoning_token_count",
                "tool_token_count",
                "ttft_ms",
            )
            inp = rec.attrs.get("input_token_count") or "?"
            outp = rec.attrs.get("output_token_count") or "?"
            reas = rec.attrs.get("reasoning_token_count") or "0"
            return [
                self._ev(
                    rec,
                    i,
                    ctx,
                    suffix="tokens",
                    kind=AgentEventKind.metric,
                    title="token usage",
                    body=f"in={inp} out={outp} reasoning={reas}",
                    data={k: str(rec.attrs[k]) for k in token_keys if rec.attrs.get(k)},
                )
            ]
        if name == "codex.turn_ttft":
            dur = str(rec.attrs.get("duration_ms") or "")
            return [
                self._ev(
                    rec,
                    i,
                    ctx,
                    suffix="ttft",
                    kind=AgentEventKind.metric,
                    title="time to first token",
                    body=f"ttft={dur}ms" if dur else "",
                    data={"duration_ms": dur} if dur else {},
                )
            ]
        if name in _TRANSPORT_EVENTS:
            # a successful api/websocket call is transport noise; a FAILED one is a real error worth
            # surfacing (the claude_code philosophy: show meaningful, drop routine).
            if str(rec.attrs.get("success") or "").lower() == "true":
                return []
            endpoint = str(rec.attrs.get("endpoint") or name.rsplit(".", 1)[-1])
            detail = str(
                rec.attrs.get("error.message")
                or rec.attrs.get("http.response.status_code")
                or "failed"
            )
            ekeys = ("endpoint", "http.response.status_code", "error.message", "attempt")
            return [
                self._ev(
                    rec,
                    i,
                    ctx,
                    suffix="err",
                    kind=AgentEventKind.status,
                    title="request failed",
                    body=f"{endpoint}: {detail}",
                    status="error",
                    severity="error",
                    data={k: str(rec.attrs[k]) for k in ekeys if rec.attrs.get(k)},
                )
            ]
        if name in _NOISE_EVENTS:
            return []
        if name.startswith("codex."):
            # An unrecognized codex.* content event: surface it so new content can't vanish
            # silently (rare — most codex.* are handled or in _NOISE_EVENTS).
            return [
                self._ev(rec, i, ctx, suffix="unknown", kind=AgentEventKind.unknown, title=name)
            ]
        return []  # non-codex log record — not ours

    def _tool_result_events(
        self, rec: FlatLogRecord, i: int, ctx: AdapterContext
    ) -> list[AgentEvent]:
        a = rec.attrs
        tool = str(a.get("tool_name") or "tool")
        call_id = str(a.get("call_id") or "")
        gid = call_id or None
        args_raw = str(a.get("arguments") or "")
        output = str(a.get("output") or "")
        success = a.get("success")
        status = None if success is None else ("ok" if str(success).lower() == "true" else "error")
        severity = "error" if status == "error" else "info"
        data = {"tool.name": tool}
        if call_id:
            data["call_id"] = call_id
        if a.get("duration_ms"):
            data["duration_ms"] = str(a["duration_ms"])
        is_mcp = str(a.get("mcp_tool") or "").lower() == "true" or "__" in tool

        # exec_command / shell -> terminal_command (+ terminal_output)
        if tool in _SHELL_TOOLS:
            cmd = self._parse_cmd(args_raw)
            evs = [
                self._ev(
                    rec,
                    i,
                    ctx,
                    suffix="cmd",
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
                        rec,
                        i,
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

        # apply_patch / write -> file_edit (the patch/content is in arguments)
        if tool in _FILE_EDIT_TOOLS:
            return [
                self._ev(
                    rec,
                    i,
                    ctx,
                    suffix="edit",
                    kind=AgentEventKind.file_edit,
                    title=tool,
                    body=args_raw or output,
                    status=status,
                    severity=severity,
                    data=data,
                    group_id=gid,
                )
            ]

        # MCP tool -> mcp_call (+ mcp_result)
        if is_mcp:
            evs = [
                self._ev(
                    rec,
                    i,
                    ctx,
                    suffix="mcp",
                    kind=AgentEventKind.mcp_call,
                    title=tool,
                    body=args_raw,
                    status=status,
                    severity=severity,
                    data=data,
                    group_id=gid,
                )
            ]
            if output:
                evs.append(
                    self._ev(
                        rec,
                        i,
                        ctx,
                        suffix="mcpres",
                        kind=AgentEventKind.mcp_result,
                        role="tool",
                        title="result",
                        body=output,
                        group_id=gid,
                    )
                )
            return evs

        # any other tool -> tool_call (+ tool_result)
        evs = [
            self._ev(
                rec,
                i,
                ctx,
                suffix="call",
                kind=AgentEventKind.tool_call,
                title=tool,
                body=args_raw,
                status=status,
                severity=severity,
                data=data,
                group_id=gid,
            )
        ]
        if output:
            evs.append(
                self._ev(
                    rec,
                    i,
                    ctx,
                    suffix="result",
                    kind=AgentEventKind.tool_result,
                    role="tool",
                    title="result",
                    body=output,
                    group_id=gid,
                )
            )
        return evs

    @staticmethod
    def _parse_cmd(args_raw: str) -> str:
        """The shell command from tool_result.arguments (JSON `{"cmd": "…"}` or `{"cmd": [..]}`).
        Total: bad JSON / missing key -> the raw arguments string (never raises)."""
        try:
            obj = json.loads(args_raw)
        except (ValueError, TypeError):
            return args_raw
        if not isinstance(obj, dict):
            return args_raw
        cmd = obj.get("cmd") or obj.get("command") or ""
        if isinstance(cmd, list):
            return " ".join(str(c) for c in cmd)
        if cmd:
            return str(cmd)
        if "chars" in obj:
            return str(obj["chars"])
        if obj.get("cell_id"):
            return f"wait for {obj['cell_id']}"
        return args_raw

    def _ev(
        self,
        rec: FlatLogRecord,
        i: int,
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
        conv = rec.attrs.get("conversation.id")
        return AgentEvent(
            run_id=ctx.run_id,
            id=f"log:{rec.raw_seq}:{i:04d}:{suffix}",
            ts=self._log_ts(rec, ctx),
            source_agent=ctx.source_agent,
            kind=kind,
            role=role,  # type: ignore[arg-type]  # always a valid Literal role
            title=title,
            body=body,
            data=data or {},
            group_id=group_id,
            conversation_id=str(conv) if conv else None,
            status=status,  # type: ignore[arg-type]  # always "ok"/"error"/None
            severity=severity,  # type: ignore[arg-type]  # always a valid Literal
            raw_ref=RawTraceRef(
                run_id=ctx.run_id,
                raw_seq=rec.raw_seq,
                span_id="",
                signal="log",
            ),
        )

    def _log_ts(self, rec: FlatLogRecord, ctx: AdapterContext) -> datetime:
        if rec.time_ns > 0:
            try:
                return datetime.fromtimestamp(rec.time_ns / 1e9, tz=UTC)
            except (OverflowError, ValueError, OSError):
                pass
        created = ctx.created_at
        return created if created.tzinfo is not None else created.replace(tzinfo=UTC)


register(CodexAdapter())
