"""Per-run telemetry fold (XOR run-report) — the display-plane stats snapshot.

DELIBERATELY separate from ``otel.stats`` (which the grader imports): this module reads the
AgentEvent projection, so it must stay off the grader's import path (.importlinter
"Grader never reads the AgentEvent projection"). It is agent-self-reported display/comparison
data, never an observed fact and never a grading input.

Pure + total: malformed token values contribute nothing and never raise. Normalizes the three
harness token-key schemas: OpenHands (gen_ai.usage.* short names), Claude Code
(bespoke), Codex (*_token_count). A flat sum over one schema returns zero for the others.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime

from xorcise.core.contracts.agent_event import AgentEvent, AgentEventKind
from xorcise.core.contracts.reporting import CountStats, RunStats, TimingStats, TokenStats

# Alias sets — one concept, many harness keys.
_INPUT = ("input_tokens", "input_token_count")
_OUTPUT = ("output_tokens", "output_token_count")
_CACHE_READ = ("cache_read_input_tokens", "cache_read_tokens", "cached_token_count")
_CACHE_CREATION = ("cache_creation_input_tokens", "cache_creation_tokens")
_REASONING = ("reasoning_token_count",)

# Event kinds that count as a "tool call" (uniform across harnesses).
_TOOL_KINDS = frozenset(
    {
        AgentEventKind.tool_call,
        AgentEventKind.mcp_call,
        AgentEventKind.terminal_command,
        AgentEventKind.file_edit,
        AgentEventKind.file_read,
        AgentEventKind.browser_action,
    }
)


def _pick_int(data: Mapping[str, str], keys: tuple[str, ...]) -> int:
    """First present alias key → int; missing / non-numeric → 0 (never raises)."""
    for k in keys:
        if k in data:
            try:
                return int(str(data[k]).strip())
            except (ValueError, TypeError):
                return 0
    return 0


def projection_key(adapter_name: str, adapter_version: str) -> str:
    """The `RunStats.projection` stamp: which adapter, at which projection version (the adapter's
    own version + the shared normalizer version, exactly as `RunEventsView.adapter_version`
    carries it), folded a snapshot. It is the agent_events cache staleness key minus the RAW
    sequence numbers: after terminate the RAW is sealed, so only a renderer change can make a
    snapshot stale."""
    return f"{adapter_name}@{adapter_version}"


def fold_run_stats(
    events: Sequence[AgentEvent],
    *,
    created_at: datetime,
    completed_at: datetime | None,
    projection: str | None = None,
) -> RunStats:
    """Fold a run's normalized event projection into a RunStats snapshot. `total` is computed
    (input+output); token keys are alias-normalized across the three harness schemas.
    `projection` (see `projection_key`) records what rendered the events being folded, so a
    reader can tell a snapshot that predates the current renderer and re-fold it."""
    tok = TokenStats()
    by_kind: Counter[str] = Counter()
    # dict, not set: insertion order is the answer. A run that switches model mid-way (a router, a
    # fallback) should read primary-first, and sorting would put whichever name happens to sort
    # lower in front of the one that did the early work.
    models: dict[str, None] = {}
    model_calls = tool_calls = findings = errors = 0
    longest_tool_ms: int | None = None
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    for e in events:
        by_kind[e.kind.value] += 1
        # Read the model off ANY event that names one, not just usage metrics. Every adapter
        # normalises its own spelling onto `model`, but they disagree about WHERE it belongs:
        # claude-code and openhands put it on each usage metric (from gen_ai.request/response
        # .model), while codex reports it once on its "session started" status event — arguably
        # the more correct place, since the model is a property of the session, not of a call.
        # Folding metrics alone silently dropped codex entirely.
        named_model = str((e.data or {}).get("model") or "").strip()
        if named_model:
            models.setdefault(named_model, None)
        if e.kind is AgentEventKind.metric:
            data = e.data or {}
            inp = _pick_int(data, _INPUT)
            out = _pick_int(data, _OUTPUT)
            if inp or out:
                model_calls += 1  # a token-bearing metric = one model call (ttft metrics excluded)
            tok.input += inp
            tok.output += out
            tok.cache_read += _pick_int(data, _CACHE_READ)
            tok.cache_creation += _pick_int(data, _CACHE_CREATION)
            tok.reasoning += _pick_int(data, _REASONING)
        if e.kind in _TOOL_KINDS:
            tool_calls += 1
        if e.kind is AgentEventKind.finding:
            findings += 1
        if e.kind is AgentEventKind.error or e.status == "error":
            errors += 1
        if e.duration_ms is not None and (
            longest_tool_ms is None or e.duration_ms > longest_tool_ms
        ):
            longest_tool_ms = e.duration_ms
        if e.ts is not None:
            first_ts = e.ts if first_ts is None or e.ts < first_ts else first_ts
            last_ts = e.ts if last_ts is None or e.ts > last_ts else last_ts

    tok.total = tok.input + tok.output
    elapsed = (completed_at - created_at).total_seconds() if completed_at else None
    return RunStats(
        models=tuple(models),
        tokens=tok,
        counts=CountStats(
            model_calls=model_calls,
            tool_calls=tool_calls,
            findings=findings,
            errors=errors,
            events_total=len(events),
            by_kind=dict(by_kind),
        ),
        timing=TimingStats(
            elapsed_seconds=elapsed,
            first_event_ts=first_ts,
            last_event_ts=last_ts,
            longest_tool_ms=longest_tool_ms,
        ),
        projection=projection,
    )
