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
from hashlib import blake2b
from itertools import islice

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


# Event kinds an adapter deliberately names a model on. The fold used to read `data["model"]` off
# ANY event, which the generic adapter turns into a leak: it copies the whole span attribute bag
# onto the event (`data=dict(span.attrs)`), so an image-generation span carrying
# `model=dall-e-3` was folded in as a model the run ran on. Each kind here has a writer:
#   metric  — otel/adapters/genai.py, the gen_ai usage metric (claude-code, openhands)
#   status  — harness_adapters/codex/otel.py, "codex.conversation_starts"
#   message — harness_adapters/claude_code/otel.py, "claude_code.assistant_response"
#   error   — harness_adapters/claude_code/otel.py, "claude_code.api_refusal"
# The generic adapter still copies the whole bag, so a GENERIC span classified `message` or
# `error` that happens to carry `model` folds in too — and its classifier reads the span NAME,
# where `model` is itself one of the `message` keywords, so `generate_image_with_model` lands on
# `message` and the image model it carries is folded. Accepted, not contained: telling that span
# from a real assistant message needs the name-sniffing this fold deliberately leaves to the
# adapters, and the residue is a bounded, self-reported display field that is never a grading
# input. What the kind filter buys is narrower than "the reviewer's case is contained", which is how
# an earlier version of this comment read: a SUCCESSFUL `generate_image` span classifies
# `unclassified` and is filtered out, but a FAILED one classifies `error` — the generic adapter
# keys that off `status_code == 2` before it ever looks at the name — and lands back inside the
# residue above. The filter removes the tool kinds and `unclassified`; it does not remove a span
# that happens to fail (#128 review).
# Add a kind here only alongside the adapter line that puts a model on it.
_MODEL_KINDS = frozenset(
    {
        AgentEventKind.metric,
        AgentEventKind.status,
        AgentEventKind.message,
        AgentEventKind.error,
    }
)

# Bounds on the model list. Every other RunStats field is a scalar; this one is a list of
# AGENT-CONTROLLED strings that is persisted in `stats_json` and returned on every `/result` —
# which `run list` and `leaderboard` call once per terminal run. Unbounded, a harness reporting
# 500 distinct names or one 200 000-character name turns a display field into a stored amplifier.
MODELS_MAX = 8
MODEL_NAME_MAX = 120
# How many DISTINCT names the fold will hold while counting the overflow. Counting "how many did
# we drop" exactly needs to remember what was already dropped, which is the unbounded set again —
# so the tracking itself is bounded and `models_truncated` saturates past this many distinct names.
# A count bound alone would not be a bound: what the fold holds PER name is bounded too, by
# `_dedupe_key` for the key and `_clip_model` for the value.
_MODELS_TRACK_MAX = 512


def _clip_model(name: str) -> str:
    """Bound one name's length for DISPLAY, marking the cut so a prefix never reads as the whole
    name. Deduping on this instead of on `_dedupe_key` merged two names agreeing on their first
    MODEL_NAME_MAX-1 characters into one, under-reporting `models_truncated` (#128 review). The
    price of separating them is that two such names render alike in `models` while still being
    counted as two."""
    return name if len(name) <= MODEL_NAME_MAX else name[: MODEL_NAME_MAX - 1] + "\u2026"


def _dedupe_key(name: str) -> str:
    """What the tracking dict keys on: the name itself while it fits the display bound, and its
    prefix plus a digest of the whole of it once it does not.

    Two bounds meet here and neither may be spent on the other. Keying on the CLIPPED name loses
    the count (see `_clip_model`). Keying on the RAW name recovers the count but turns
    `_MODELS_TRACK_MAX` from a bound on how many SHORT strings the fold holds into a bound on how
    many arbitrarily long ones — _MODELS_TRACK_MAX live copies of a 200 000-character name, the
    amplifier these bounds exist to stop, and not copies shared with the events either, because the
    `.strip()` upstream returns a fresh string whenever the harness pads the value. A digest is
    bounded and still tells the two names apart. Keeping the prefix in front is what holds the key
    longer than MODEL_NAME_MAX, so a long name's key cannot collide with a short name that happens
    to look like a digest.
    """
    if len(name) <= MODEL_NAME_MAX:
        return name
    digest = blake2b(name.encode("utf-8", "surrogatepass"), digest_size=16).hexdigest()
    return f"{name[:MODEL_NAME_MAX]}:{digest}"


def _pick_int(data: Mapping[str, str], keys: tuple[str, ...]) -> int:
    """First present alias key → int; missing / non-numeric → 0 (never raises)."""
    for k in keys:
        if k in data:
            try:
                return int(str(data[k]).strip())
            except (ValueError, TypeError):
                return 0
    return 0


# The fold's OWN output shape, versioned independently of the adapter that produced the events.
# Bump whenever fold_run_stats starts emitting a field it did not emit before: the adapter name
# and version do not change when a field is ADDED here, so without this a snapshot folded before
# the new field existed keeps matching the current stamp and is served as-is — stale, and
# indistinguishable from fresh. v2: RunStats.models.
STATS_FOLD_VERSION = "stats.2"


def projection_key(adapter_name: str, adapter_version: str) -> str:
    """The `RunStats.projection` stamp: which adapter, at which projection version (the adapter's
    own version + the shared normalizer version, exactly as `RunEventsView.adapter_version`
    carries it), folded a snapshot. It is the agent_events cache staleness key minus the RAW
    sequence numbers: after terminate the RAW is sealed, so only a renderer change can make a
    snapshot stale."""
    return f"{adapter_name}@{adapter_version}+{STATS_FOLD_VERSION}"


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
    # lower in front of the one that did the early work. Keyed by `_dedupe_key`, valued by the
    # display name, because deduping on the clipped name collapsed two long names that share a
    # prefix down to one (#128 review).
    models: dict[str, str] = {}
    model_calls = tool_calls = findings = errors = 0
    longest_tool_ms: int | None = None
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    for e in events:
        by_kind[e.kind.value] += 1
        # Read the model off the kinds an adapter NAMES one on, not off every event. Adapters
        # disagree about where it belongs — claude-code and openhands put it on each usage metric
        # (from gen_ai.request/response.model), codex once on its "session started" status event,
        # and claude-code also on an assistant message and an api_refusal — so folding metrics
        # alone dropped codex entirely. Folding everything went too far the other way: the generic
        # adapter copies the whole span attribute bag onto the event, so a `generate_image` tool
        # call carrying `model=dall-e-3` was reported as a model the run ran on. _MODEL_KINDS is
        # the evidenced middle.
        if e.kind in _MODEL_KINDS:
            named_model = str((e.data or {}).get("model") or "").strip()
            if named_model and len(models) < _MODELS_TRACK_MAX:
                key = _dedupe_key(named_model)
                if key not in models:
                    models[key] = _clip_model(named_model)
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
        models=tuple(islice(models.values(), MODELS_MAX)),
        models_truncated=max(0, len(models) - MODELS_MAX),
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
