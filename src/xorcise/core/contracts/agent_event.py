"""Normalized agent-replay display DTOs (LEAF). The agent-agnostic product model the
Replay UI renders; adapters produce it from RAW OTLP. Imports stdlib + pydantic only.
RAW OTLP stays canonical; this projection is a rebuildable, adapter-versioned cache that
the grader must never read."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentEventKind(StrEnum):
    """Closed render set. Framework-specific meaning rides `subkind`/`data`, never a new kind."""

    message = "message"
    thinking = "thinking"
    terminal_command = "terminal_command"
    terminal_output = "terminal_output"
    file_edit = "file_edit"
    file_read = "file_read"
    browser_action = "browser_action"
    browser_observation = "browser_observation"
    tool_call = "tool_call"
    tool_result = "tool_result"
    mcp_call = "mcp_call"
    mcp_result = "mcp_result"
    finding = "finding"
    flag = "flag"
    error = "error"
    status = "status"
    metric = "metric"
    unknown = "unknown"


class KindSupport(StrEnum):
    """Honest per-kind support: `partial` = emitted only in a restricted form (note REQUIRED)."""

    supported = "supported"
    partial = "partial"
    unsupported = "unsupported"


class HarnessCapabilityProfile(_Frozen):
    """A harness adapter's declared telemetry capabilities — TOTAL over AgentEventKind.

    Declared beside each adapter's mapping code (the same MR that teaches an adapter a new
    span flips the entry). `notes` carry the honest user-facing gap sentences, rendered
    verbatim in the UI and the judge disclosure so the wording never forks. `verified` is
    False only for the generic fallback (profile not audited against a real harness)."""

    adapter_name: str
    adapter_version: str
    verified: bool = True
    kinds: Mapping[str, KindSupport]
    # Messages share one normalized event kind, but registration needs to distinguish whether
    # the harness exports the operator prompt, the agent response, or both.
    message_roles: Mapping[str, KindSupport] = Field(default_factory=dict)
    notes: Mapping[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _total_and_noted(self) -> HarnessCapabilityProfile:
        expected = {k.value for k in AgentEventKind}
        got = set(self.kinds)
        if missing := expected - got:
            raise ValueError(f"profile not total — missing kinds: {sorted(missing)}")
        if unknown := got - expected:
            raise ValueError(f"profile has unknown kinds: {sorted(unknown)}")
        unnoted = [
            k for k, v in self.kinds.items() if v is KindSupport.partial and k not in self.notes
        ]
        if unnoted:
            raise ValueError(f"partial kinds require a note: {sorted(unnoted)}")
        return self


class HarnessLaunchPreview(_Frozen):
    """Static, registration-safe preview of how a harness will be started.

    This is deliberately a TEMPLATE, not a runnable launch profile: run credentials, endpoints,
    correlation values, and the real mission only exist after a run is created.
    """

    launch_modes: tuple[Literal["host", "container"], ...]
    command_template: str | None = None
    model_flag: str | None = None
    model_flag_anchor: str | None = None
    tips: tuple[str, ...] = ()
    mission_preamble: tuple[str, ...] = ()


class HarnessDescriptor(_Frozen):
    """One registration-facing harness descriptor assembled from the harness-owned planes."""

    kind: str
    display_name: str
    description: str
    model_hints: tuple[str, ...] = ()
    capabilities: HarnessCapabilityProfile
    launch: HarnessLaunchPreview


class RawTraceRef(_Frozen):
    """Provenance back to the RAW trace record a display event was derived from."""

    run_id: str
    raw_seq: int
    span_id: str
    trace_id: str | None = None
    signal: Literal["trace", "log"] = "trace"


class EventCursor(_Frozen):
    """Independent exclusive cursors for the two OTLP signals.

    Trace and log export batches are numbered independently, so their sequence numbers must never
    be compared to each other.
    """

    trace_seq: int = -1
    log_seq: int = -1


class AgentEvent(_Frozen):
    """One normalized, display-only event. Agent-agnostic: `source_agent` is provenance,
    not a rendering switch. Derived + rebuildable from RAW OTLP; never a grading input."""

    run_id: str
    id: str
    ts: datetime
    # Server-side OTLP receipt time for the RAW export batch that produced this event. This is the
    # cross-plane timeline clock; `ts` remains the producer clock used for display and intra-batch
    # order.
    received_at: datetime | None = None
    source_agent: str
    kind: AgentEventKind
    subkind: str | None = None
    role: Literal["agent", "user", "system", "tool"] = "agent"
    title: str
    body: str = ""
    data: Mapping[str, str] = Field(default_factory=dict)
    group_id: str | None = None
    parent_id: str | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    conversation_id: str | None = None
    duration_ms: int | None = None
    status: Literal["ok", "error"] | None = None
    severity: Literal["info", "notice", "warning", "error"] = "info"
    raw_ref: RawTraceRef


class AdapterWarning(_Frozen):
    """A non-fatal normalization signal (unknown span, parse fallback, source mismatch)."""

    code: str
    message: str
    span_id: str | None = None
    count: int = 1


class RunEventsView(_Frozen):
    """The `GET /runs/{id}/events` page."""

    run_id: str
    source_agent: str
    adapter_name: str
    adapter_version: str
    fallback: bool
    # Legacy trace-only cursor retained for API compatibility. New clients must use next_cursor.
    next_since: int
    next_cursor: EventCursor = Field(default_factory=EventCursor)
    counts: Mapping[str, int] = Field(default_factory=dict)
    warnings: tuple[AdapterWarning, ...] = ()
    events: tuple[AgentEvent, ...] = ()
