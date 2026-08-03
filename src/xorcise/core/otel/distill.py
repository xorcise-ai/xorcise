"""Distill the sealed RAW OTel trace into an actions-only transcript for the LLM judge.

The raw OTLP payloads are ~90% noise for grading: every span repeats an identity block
(run_id/user/session/organization/terminal) and telemetry (token counts, durations, request ids,
ttft, stop reasons), and whole spans (llm_request, tool.blocked_on_user, tool.execution) carry only
plumbing. What the judge needs is what the agent *did* — the tool commands and their outputs — the
same signal the Replay UI surfaces. This produces that view, but from the RAW trace: the grader may
NOT read the display AgentEvent projection (otel.adapters / harness_adapters), so this is
a parallel, RAW-derived distillation, not the replay path.

Approach: flatten each RAW payload (otel.flatten) and, per span, keep ONLY content-bearing
attributes — first screening out harness PROVENANCE namespaces (code./thread./process./…, whose
leaf segments collide with the allowlist's generic words), then applying an allowlist by key segment
(commands, output, prompts, urls, tool names, …). Everything else is dropped, repeated values are
deduped (a command echoed in both an attr and an event), and any span left with no content (the
telemetry-only lifecycle spans) is dropped entirely. Pure + total: imports otel.flatten + stdlib
only and never raises.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.flatten import FlatLogRecord, FlatSpan, flatten, flatten_logs

# Content-bearing attribute segments (the part after the last '.', or the whole key). ONLY these
# survive — everything else (identity, telemetry, ids, timings, token counts) is dropped by default.
# Segment-exact (not substring) so "gen_ai.response.finish_reasons" (seg "finish_reasons") is noise
# while "gen_ai.completion" (seg "completion") is kept.
_CONTENT_SEGMENTS = frozenset(
    {
        # shell / commands
        "command",
        "full_command",
        "bash_command",
        "cmd",
        # results / IO
        "output",
        "stdout",
        "stderr",
        "result",
        "response",
        # free text
        "content",
        "text",
        "body",
        "message",
        "prompt",
        "completion",
        "summary",
        "detail",
        "reason",
        "description",
        # web / network actions
        "url",
        "uri",
        "target",
        "path",
        "route",
        "method",
        "query",
        "statement",
        # tool / call shape
        "tool_name",
        "name",
        "function",
        "arguments",
        "input",
        "params",
        # mission / outcome
        "objective",
        "flag",
        "title",
        "error",
    }
)


# PROVENANCE namespaces (OTel resource/code conventions + Rust `tracing`): attributes describing the
# HARNESS's own source, thread and process — never the workload under evaluation. Screened out
# BEFORE the content allowlist, because that allowlist matches the LAST key segment and its generics
# collide head-on here: `code.module.name` and `thread.name` end in "name" (allowed for tool_name)
# and `code.file.path` ends in "path" (allowed for api.path).
#
# That collision is what made the judge unusable on codex, whose spans are almost entirely plumbing:
# on a live 16,832-span run these keys were 1,570,424 of 1,573,499 kept characters (99.8%), so the
# judge prompt was four Rust source paths repeated thousands of times — 991,150 tokens against a
# 272,000-token model ceiling, and every codex run scored judge=0.0. Denying whole NAMESPACES rather
# than listing exact keys is what keeps holding as harness adapters multiply: these registries are
# provenance by definition, whatever leaf segment a given harness ends them in.
_PROVENANCE_NAMESPACES = frozenset(
    {"code", "thread", "process", "host", "os", "container", "service", "telemetry"}
)

# Rust `tracing` emits a BARE `target` carrying the emitting module path, which collides with
# `http.target` — a genuine request path. Same word, opposite meaning: the namespace tells them
# apart, so the qualified key survives the allowlist and the unqualified one is dropped here.
_PROVENANCE_EXACT = frozenset({"target"})


def _is_provenance(key: str) -> bool:
    return key in _PROVENANCE_EXACT or key.split(".", 1)[0] in _PROVENANCE_NAMESPACES


def _is_content(key: str) -> bool:
    if _is_provenance(key):
        return False
    return key.rsplit(".", 1)[-1] in _CONTENT_SEGMENTS


# OTLP AnyValue markers. An attribute whose VALUE is itself a serialized OTLP/JSON envelope (e.g.
# Laminar's `lmnr.span.path = '{"values":[{"stringValue":"conversation"}, ...]}'`) is double-encoded
# telemetry, not agent action — the allowlist keeps it only because its key's last segment ("path")
# happens to match. Such blobs are pure noise AND disproportionately large (measured: the biggest
# spans on a real 750KB run were dominated by them), so they are dropped. Matching is conservative:
# the value must LOOK like a JSON container AND carry an AnyValue key marker, which real command
# output / prose never does.
_TELEMETRY_MARKERS = (
    '"stringValue"',
    '"intValue"',
    '"boolValue"',
    '"doubleValue"',
    '"arrayValue"',
    '"kvlistValue"',
    '"values"',
)


def _looks_like_serialized_telemetry(value: str) -> bool:
    stripped = value.lstrip()
    return stripped[:1] in ("{", "[") and any(m in value for m in _TELEMETRY_MARKERS)


def _render(attrs: Mapping[str, str], seen: set[str]) -> list[str]:
    """Render content-bearing `k=v` parts, skipping values already shown on this span (dedupe) and
    serialized-telemetry blobs (double-encoded OTLP that slipped past the key allowlist)."""
    parts: list[str] = []
    for key, value in attrs.items():
        if _is_content(key) and value not in seen and not _looks_like_serialized_telemetry(value):
            seen.add(value)
            parts.append(f"{key}={value}")
    return parts


def _render_span(span: FlatSpan) -> str | None:
    """One compact line for a span, or None when it carries no agent-action content."""
    seen: set[str] = set()
    attr_parts = _render(span.attrs, seen)
    event_parts: list[str] = []
    for ev in span.events:
        ev_parts = _render(ev.attrs, seen)
        if ev_parts:
            event_parts.append(f"⟨{ev.name}: {' · '.join(ev_parts)}⟩")
    if not attr_parts and not event_parts:
        return None  # telemetry-only / lifecycle span — nothing the judge needs
    segments = [span.name or "(unnamed)"]
    if attr_parts:
        segments.append(" · ".join(attr_parts))
    segments.extend(event_parts)
    return " | ".join(segments)


def _render_log(rec: FlatLogRecord) -> str | None:
    """One compact line for a LOG record, or None when it carries no agent-action content.

    Seeded with the event name so a record whose `event.name` attr repeats it does not say it
    twice — `event.name` is itself content-bearing (segment "name"), unlike a span's name."""
    seen: set[str] = {rec.event_name}
    parts = _render(rec.attrs, seen)
    if not parts:
        return None
    return " | ".join([rec.event_name or "(log)", " · ".join(parts)])


def distill_transcript(
    records: Sequence[TraceRecord], log_records: Sequence[TraceRecord] = ()
) -> tuple[str, ...]:
    """Raw trace + log records -> ordered, actions-only lines for the judge prompt, ONE ELEMENT PER
    SPAN OR LOG RECORD.

    BOTH RAW signals are read, because which one carries the agent's actions is a per-harness
    choice: claude-code puts shell commands and outputs on span events, while codex reports every
    tool call ONLY on the logs signal (`codex.tool_result` records with `arguments` + `output`) and
    fills its spans with plumbing. Reading spans alone made the judge blind on codex — a run that
    wrote, compiled and ran an HTTP proxy was graded "no evidence of an HTTP forward proxy
    implementation" — and cost claude-code its log-borne evidence too. Both stores are RAW and
    canonical, so this is not the forbidden AgentEvent projection.

    Records are sorted by seq within each signal, then the two are merged CHRONOLOGICALLY on the
    span start / record timestamp, so the judge reads one ordered narrative rather than two piles
    grouped by transport. Ties fall back to (raw_seq, arrival) to stay deterministic. Telemetry-only
    spans/records and malformed payloads contribute nothing. The one-element-per-item contract is
    what lets the judge-prompt builder wrap each in ⟦span N⟧ boundary markers."""
    items: list[tuple[int, int, int, str]] = []  # (time_ns, raw_seq, arrival, line)
    arrival = 0
    for rec in sorted(records, key=lambda r: r.seq):
        try:
            payload = json.loads(rec.payload)
        except (json.JSONDecodeError, ValueError):
            continue  # malformed raw record — contribute nothing (stay total)
        for span in flatten(payload, raw_seq=rec.seq):
            line = _render_span(span)
            if line is not None:
                items.append((span.start_ns, rec.seq, arrival, line))
                arrival += 1
    for rec in sorted(log_records, key=lambda r: r.seq):
        try:
            payload = json.loads(rec.payload)
        except (json.JSONDecodeError, ValueError):
            continue
        for log in flatten_logs(payload, raw_seq=rec.seq):
            line = _render_log(log)
            if line is not None:
                items.append((log.time_ns, rec.seq, arrival, line))
                arrival += 1
    items.sort(key=lambda item: item[:3])
    return tuple(line for *_, line in items)
