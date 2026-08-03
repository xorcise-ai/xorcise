"""v2 BYOM terrain attribution — the runs-module half.

PURE prompt-building + verdict-parsing; the model is an INJECTED `Scorer` (the delivery layer
builds the httpx client — this module never imports httpx or a sibling store/module). Unlike v1
(which let the model invent src/dst/edge_kind against a bare node list), v2 is UPDATE-ONLY: the
model may only mark AUTHORED elements (nodes/groups/edges from the projected v2 graph) as
discovered/completed/active, never create new ones. A malformed model response degrades to
`is_an_action=False` (never an exception); a hard parse failure returns `None` (retryable —
nothing gets cached), mirroring v1's `parse_verdicts` contract exactly.

The resolved contract types (`TerrainGroup`/`TerrainNodeV2`, `contracts/terrain.py`) carry the
authored `discovery_condition`/`completion_condition` text alongside `description` (the
projector's `_authored()` in `runs/terrain_v2.py` maps both from the authored dicts); the
grounding below renders whichever of description/conditions are present, per element."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from xorcise.core.contracts.agent_event import AgentEvent
from xorcise.core.contracts.mission import RubricCriterion
from xorcise.core.contracts.terrain import TerrainEdgeV2, TerrainGroup, TerrainNodeV2
from xorcise.core.runs.terrain_attribution import _ATTRIBUTABLE_KINDS  # reuse v1's 18-minus-4 set

_NODE_STATES = {"discovered", "completed"}

_SYSTEM_HEADER = """You map a cyber-agent's actions onto an AUTHORED mission terrain graph. You
may ONLY mark elements from the AUTHORED GRAPH below as discovered, completed, or active — you
may NOT invent new nodes, groups, or edges, and any id outside the AUTHORED GRAPH is ignored.

For each event, decide whether it is a meaningful action against the graph (`is_an_action`) and,
if so, which authored elements it discovers, completes, or activates.

SUCCESS SIGNALS ARE ACTIONS: if an event's output contains a flag (e.g. `XORCISE{...}`) or
otherwise satisfies an authored node's "completed when" condition, that event IS an action —
mark that node "completed". Recovering the objective's success signal completes the objective
node; never mark such an event "not an action".

Each event is labelled with a number in brackets (e.g. `[1]`); use that number as `event_id`.

Reply with a JSON array; one object per event you can evaluate:
{"event_id": <the number in brackets, e.g. 1>, "is_an_action": <bool>,
 "note": <string, only when is_an_action is true>,
 "update": {
   "nodes":  [{"id": <node id>, "state": "discovered"|"completed"}],
   "groups": [{"id": <group id>, "discovered": true}],
   "edges":  [{"id": <edge id>, "active": true}]
 }}
"is_an_action" is false (and "update" empty or omitted) when the event has no terrain meaning —
e.g. it doesn't touch any authored element, or merely repeats ground already covered. When
"is_an_action" is true, also set "note" to a concise, operator-facing description of WHAT THE
AGENT DID in this span (e.g. "used the SSRF /fetch pivot to reach internal:8080"), 120 characters
or fewer; omit or set "note" to null when "is_an_action" is false. Reply with ONLY the JSON
array."""


_REPAIR_INSTRUCTION = (
    "\n\nYour previous reply could not be parsed as a JSON array. Reply with ONLY the JSON array "
    "specified above — no prose, no explanation, no markdown fences; nothing before the opening "
    "`[` or after the closing `]`."
)


def _repair_user(user: str, bad_reply: str) -> str:
    """Build the one-shot repair prompt: the original evidence, the model's own invalid reply fed
    back so it can see what went wrong, and a restated format contract."""
    return f"{user}\n\nPREVIOUS INVALID REPLY:\n{bad_reply[:500]}{_REPAIR_INSTRUCTION}"


class Scorer(Protocol):
    """The injected model call — same shape as v1's `Scorer`, redefined locally so this module
    never imports the v1 module for anything but the shared `_ATTRIBUTABLE_KINDS` constant."""

    def score(self, messages: Sequence[tuple[str, str]]) -> str: ...


@dataclass(frozen=True, slots=True)
class ElementUpdate:
    """One authored-element mutation attributed to a span. `target_id` is guaranteed (by
    `parse_verdicts_v2`) to be a member of the closed `known_ids` vocabulary the caller passed in
    — never a model-invented id. Exactly one of the trailing fields is meaningful, selected by
    `target_kind`:
    - `"node"`:  `state` in {"discovered", "completed"} (the fold's advance-only semantics are
      enforced client-side, not here).
    - `"group"`: `discovered=True`.
    - `"edge"`:  `active=True`.
    Field names/shape mirror `terrain_update_store._UpdateInput` (minus `event_id`, which lives on
    the enclosing `SpanVerdict` since one span can emit several updates) so the delivery layer
    can zip them 1:1 into `record_many` calls."""

    target_kind: Literal["node", "group", "edge"]
    target_id: str
    state: Literal["discovered", "completed"] | None = None
    discovered: bool | None = None
    active: bool | None = None


@dataclass(frozen=True, slots=True)
class SpanVerdict:
    """One event's attribution verdict. TOTAL: `parse_verdicts_v2` returns exactly one
    `SpanVerdict` per input event, even when the model said nothing about it or every update it
    proposed got filtered out — `is_an_action=False, updates=()` in that case, so the caller can
    still record the span as "considered" (via `TerrainUpdateStore.record_considered`) rather than
    leaving it perpetually pending."""

    event_id: str
    is_an_action: bool
    updates: tuple[ElementUpdate, ...] = ()
    note: str | None = None


@dataclass(frozen=True, slots=True)
class PromptContext:
    """Bundles the authored-graph + rubric grounding that `attribute_batch_v2` threads through to
    `build_attribution_prompt_v2` on every call, so the batch driver's own signature doesn't grow
    a parameter per grounding input."""

    summary: str | None
    rubric: Sequence[RubricCriterion]
    groups: Sequence[TerrainGroup]
    nodes: Sequence[TerrainNodeV2]
    edges: Sequence[TerrainEdgeV2]


# Output-bearing kinds carry results, banners, and (critically) recovered flags — the success
# signals the model must see to complete a node. Commands/calls are terse by nature; a tight cap
# keeps the prompt small. A success signal (a flag) routinely lands at the END of long output, so
# the old uniform 400-char cap silently buried it — the top cause of missed objective completions.
_OUTPUT_KINDS = frozenset(
    {
        "terminal_output",
        "tool_result",
        "mcp_result",
        "browser_observation",
        "file_read",
        "finding",
        "flag",
        "error",
        "status",
    }
)
_OUTPUT_BODY_CAP = 2000
_DEFAULT_BODY_CAP = 400


def _event_line(e: AgentEvent, idx: int | None = None) -> str:
    body = (e.body or e.title or "").strip().replace("\n", " ")
    cap = _OUTPUT_BODY_CAP if e.kind.value in _OUTPUT_KINDS else _DEFAULT_BODY_CAP
    # Batch events carry a 1-based bracket NUMBER the model echoes back as `event_id` (see
    # `parse_verdicts_v2`); prior context is unlabelled (the model never attributes it). Numbering
    # replaces the raw base64 span id the model kept mangling — there is now no id to corrupt.
    prefix = f"[{idx}]" if idx is not None else "-"
    return f"{prefix} {e.kind.value}: {body[:cap]}"


def build_attribution_prompt_v2(
    summary: str | None,
    rubric: Sequence[RubricCriterion],
    groups: Sequence[TerrainGroup],
    nodes: Sequence[TerrainNodeV2],
    edges: Sequence[TerrainEdgeV2],
    events: Sequence[AgentEvent],
    context: Sequence[AgentEvent],
) -> tuple[str, str]:
    """Build the (system, user) prompt pair. `system` is ALL trusted grounding — the mission
    summary, the rubric's expected steps, and the authored graph (the closed id vocabulary) plus
    the JSON verdict contract. `user` is the UNTRUSTED evidence — the agent's own spans — so a
    prompt-injection payload inside a span body can never masquerade as grounding.

    `context` must already be sliced to the short window IMMEDIATELY BEFORE `events` in run order
    (the caller's job — see `attribute_batch_v2`); this fixes v1's future-skew Minor, where the
    prior-context slice could include events that occurred AFTER the batch being judged. This
    function still defensively takes only the last 5 of whatever `context` it's given."""
    rubric_lines = [f"- {c.id}: {c.text}" for c in rubric]
    group_lines = [
        f"- {g.id} (group): {g.description or g.label}"
        + (f" | discovered when: {g.discovery_condition}" if g.discovery_condition else "")
        for g in groups
    ]
    node_lines = [
        f"- {n.id} (node, in group {n.group}): {n.description or n.label}"
        + (f" | discovered when: {n.discovery_condition}" if n.discovery_condition else "")
        + (f" | completed when: {n.completion_condition}" if n.completion_condition else "")
        for n in nodes
    ]
    edge_lines = [
        f"- {e.id} (edge): {e.src} -> {e.dst}" + (f" — {e.label}" if e.label else "") for e in edges
    ]
    system = (
        _SYSTEM_HEADER
        + "\n\nMISSION SUMMARY:\n"
        + (summary or "(none)")
        + "\n\nEXPECTED STEPS (rubric):\n"
        + ("\n".join(rubric_lines) or "(none)")
        + "\n\nAUTHORED GRAPH (the ONLY ids you may reference):\nGroups:\n"
        + ("\n".join(group_lines) or "(none)")
        + "\nNodes:\n"
        + ("\n".join(node_lines) or "(none)")
        + "\nEdges:\n"
        + ("\n".join(edge_lines) or "(none)")
    )
    ctx = "\n".join(_event_line(e) for e in context[-5:])
    batch = "\n".join(_event_line(e, i + 1) for i, e in enumerate(events))
    user = (f"PRIOR CONTEXT:\n{ctx}\n\n" if ctx else "") + f"EVENTS TO ATTRIBUTE:\n{batch}"
    return system, user


def _extract_updates(update_obj: object, known_ids: set[str]) -> tuple[ElementUpdate, ...]:
    """Update-only guard: total, never raises. Drops any entry whose `id` is missing, not a
    string, or not in `known_ids` (never create); clamps node `state` to {discovered, completed},
    group to `discovered:true`, edge to `active:true`; any other shape (wrong types, missing
    keys, non-list collections) is ignored rather than raising."""
    if not isinstance(update_obj, dict):
        return ()
    out: list[ElementUpdate] = []

    raw_nodes = update_obj.get("nodes")
    for item in raw_nodes if isinstance(raw_nodes, list) else []:
        if not isinstance(item, dict):
            continue
        tid = item.get("id")
        state = item.get("state")
        if not isinstance(tid, str) or tid not in known_ids or state not in _NODE_STATES:
            continue
        out.append(ElementUpdate(target_kind="node", target_id=tid, state=state))

    raw_groups = update_obj.get("groups")
    for item in raw_groups if isinstance(raw_groups, list) else []:
        if not isinstance(item, dict):
            continue
        tid = item.get("id")
        if not isinstance(tid, str) or tid not in known_ids or item.get("discovered") is not True:
            continue
        out.append(ElementUpdate(target_kind="group", target_id=tid, discovered=True))

    raw_edges = update_obj.get("edges")
    for item in raw_edges if isinstance(raw_edges, list) else []:
        if not isinstance(item, dict):
            continue
        tid = item.get("id")
        if not isinstance(tid, str) or tid not in known_ids or item.get("active") is not True:
            continue
        out.append(ElementUpdate(target_kind="edge", target_id=tid, active=True))

    return tuple(out)


def _norm_event_id(eid: str) -> str:
    """Normalize an event id for tolerant matching. Event ids are `<base64-span>=:<kind>` (e.g.
    `AtyzySn0lBk=:tool`), but the model routinely echoes the id back WITHOUT its `:<kind>` suffix
    and/or the base64 `=` padding (e.g. `AtyzySn0lBk`). Matching a verdict to its event by the raw
    string therefore silently fails and the update is dropped — the top cause of "no attributions
    landing". Stripping the `:<kind>` suffix + trailing `=` on both sides makes a mangled id match
    (the base64 alphabet has no `:`, so the rsplit is safe)."""
    return eid.rsplit(":", 1)[0].rstrip("=")


def parse_verdicts_v2(
    raw: str, events: Sequence[AgentEvent], known_ids: set[str]
) -> list[SpanVerdict] | None:
    """Map the model's JSON array to one `SpanVerdict` per event (by id). Total: never raises.

    Returns `None` IFF `raw` could not be parsed as a top-level JSON array — a HARD failure the
    caller must treat as retryable (persist/cache nothing, re-send next poll; mirrors v1's
    `parse_verdicts`). A successful parse — including a valid empty array `[]` — always returns a
    list, one verdict per event; a missing/malformed per-event entry, an entry whose every update
    got dropped by the update-only guard, or `is_an_action: false` all yield
    `is_an_action=False, updates=()` for that event (which IS safe to cache: the model considered
    the event and it produced no terrain change).

    Matching is by exact `event_id` first, then by NORMALIZED id (`_norm_event_id`) so a verdict
    whose id the model mangled (dropped `=`/`:kind`) still lands on its event. Two sub-events of one
    span (`…:out` / `…:tool`) share a normalized key, so a single span-level verdict correctly
    applies to both — the update targets a node/edge, not a specific sub-event."""
    verdicts: dict[str, dict[str, object]] = {}
    norm_verdicts: dict[str, dict[str, object]] = {}
    try:
        parsed = json.loads(raw[raw.index("[") : raw.rindex("]") + 1])
        if not isinstance(parsed, list):
            return None
        for item in parsed:
            eid = item.get("event_id") if isinstance(item, dict) else None
            if isinstance(eid, (str, int)) and not isinstance(eid, bool):
                vid = str(eid)
                verdicts[vid] = item
                norm_verdicts.setdefault(_norm_event_id(vid), item)
    except (ValueError, KeyError, RecursionError):
        return None

    out: list[SpanVerdict] = []
    for i, e in enumerate(events):
        # Match order: the 1-based batch INDEX the prompt labelled each event with (primary — the
        # model can't mangle a number), then the exact raw id, then the normalized id (tolerates a
        # model that still echoes and mangles the base64 span id). See build_attribution_prompt_v2.
        v = verdicts.get(str(i + 1)) or verdicts.get(e.id)
        if v is None:
            v = norm_verdicts.get(_norm_event_id(e.id))
        if v is None or v.get("is_an_action") is not True:
            out.append(SpanVerdict(event_id=e.id, is_an_action=False, updates=()))
            continue
        updates = _extract_updates(v.get("update"), known_ids)
        if not updates:  # no surviving update -> not actually an action, per the brief
            out.append(SpanVerdict(event_id=e.id, is_an_action=False, updates=()))
            continue
        raw_note = v.get("note")
        note = raw_note.strip()[:200] if isinstance(raw_note, str) and raw_note.strip() else None
        out.append(SpanVerdict(event_id=e.id, is_an_action=True, updates=updates, note=note))
    return out


def attribute_batch_v2(
    events: Sequence[AgentEvent],
    attributed_ids: set[str],
    known_ids: set[str],
    prompt_ctx: PromptContext,
    score: Scorer,
    limit: int = 8,
    max_prompt_tokens: int | None = None,
    count_tokens: Callable[[str], int] | None = None,
) -> list[SpanVerdict]:
    """Attribute up to `limit` oldest un-attributed action-events in ONE model call. Returns the
    parsed verdicts (caller persists via the update store). Empty (and no model call) when nothing
    is pending; empty (but a model call did happen) when the response is a hard parse failure —
    mirrors v1's `attribute_batch` degrade path exactly.

    `events` must be in run (chronological) order. The prior-context slice passed to
    `build_attribution_prompt_v2` is every event strictly BEFORE the batch's earliest member in
    `events` — never events that occur later, even if those later events are also un-attributed —
    fixing v1's future-skew Minor.

    TOKEN SAFETY CAP: when both `max_prompt_tokens` and `count_tokens` are given, the batch is
    SHRUNK (trailing events dropped) until the built prompt (system + user) fits the cap — so a
    small-context attribution model never gets an over-long prompt. At least one event is always
    kept (a single event over the cap is still sent — dropping it would stall the drain; its body
    is already char-capped by `_event_line`)."""
    pending_idx = [
        i
        for i, e in enumerate(events)
        if e.kind.value in _ATTRIBUTABLE_KINDS and e.id not in attributed_ids
    ]
    if not pending_idx:
        return []
    batch_idx = pending_idx[:limit]
    context = events[: batch_idx[0]]

    def _build(idxs: list[int]) -> tuple[list[AgentEvent], str, str]:
        b = [events[i] for i in idxs]
        s, u = build_attribution_prompt_v2(
            prompt_ctx.summary,
            prompt_ctx.rubric,
            prompt_ctx.groups,
            prompt_ctx.nodes,
            prompt_ctx.edges,
            b,
            context,
        )
        return b, s, u

    batch, system, user = _build(batch_idx)
    if max_prompt_tokens is not None and count_tokens is not None:
        while len(batch_idx) > 1 and count_tokens(system) + count_tokens(user) > max_prompt_tokens:
            batch_idx = batch_idx[:-1]
            batch, system, user = _build(batch_idx)
    raw = score.score([("system", system), ("user", user)])
    verdicts = parse_verdicts_v2(raw, batch, known_ids)
    if verdicts is None:
        # Hard parse failure (not a JSON array — often a reasoning-model preamble). Push back ONCE
        # with a repair prompt that feeds the bad reply back and restates the contract, then
        # re-parse. Bounded to one retry: if the repair turn also fails, degrade to [] (retryable).
        verdicts = parse_verdicts_v2(
            score.score([("system", system), ("user", _repair_user(user, raw))]), batch, known_ids
        )
    return verdicts if verdicts is not None else []
