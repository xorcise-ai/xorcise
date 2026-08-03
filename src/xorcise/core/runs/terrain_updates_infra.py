"""Deterministic v2 terrain-update producer (XORCISE-infra plane) — PURE.

v2 analog of `terrain_actions.infra_actions` (read that module first): same first-party
evidence (a run's ObservedFacts + submissions, passed as PLAIN DATA by the delivery layer,
never via a sibling store), but emits v2 UPDATE inputs (`_UpdateInput`, event_id=None)
targeting the FIXED infra nodes/edges `project_terrain_v2` always projects
(`runs/terrain_v2.py::_infra_scaffold`) instead of v1 TerrainActions. The mission plane (BYOM
attribution) is `terrain_attribution_v2.py`; this module never calls a model.

Each update is returned PAIRED WITH ITS SERVER-CLOCK ANCHOR — the `created_at` of the driving
record (join/prompt/attachment fact, or artifact/intel/complete submission), the first-span receipt
(`telemetry_ts`), or `None` when unknown (the delivery layer sorts `None` last). Anchoring happens
here, at production time, because a single edge (`m:agent-rc`) is lit by SEVERAL run-control
interactions that each need their own timestamp — a target-id → timestamp heuristic can't tell them
apart. The unified receipt-time timeline (`terrain_timeline.order_updates`) interleaves these with
the mission plane so time-travel shows the correct last run-control action at every fold position.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from xorcise.core.contracts.agent_event import AgentEvent
from xorcise.core.contracts.telemetry import ObservedFact
from xorcise.core.runs.terrain_update_store import _UpdateInput

_ARTIFACT_KINDS = frozenset({"artifact", "flag"})

# Fixed infra ids from `_infra_scaffold()` in runs/terrain_v2.py — never authored, always present.
_AGENT_NODE = "agent"
_HS_JOIN_NODE = "hs:join"
_AGENT_HS_EDGE = "m:agent-hs"
_RC_PROMPT_NODE = "rc:prompt"
_RC_ATTACHMENTS_NODE = "rc:attachments"
_RC_INTEL_NODE = "rc:intel"
_RC_ARTIFACTS_NODE = "rc:artifacts"
_RC_DONE_NODE = "rc:done"
_AGENT_RC_EDGE = "m:agent-rc"
_COLLECTOR_NODE = "collector"
_AGENT_COLLECTOR_EDGE = "m:agent-collector"

# Static, concise operator-facing labels (restores v1's infra labels) — display-only.
_HS_JOIN_NOTE = "Agent joined the tailnet"
_COLLECTOR_NOTE = "Telemetry flowing to the OTel collector"
_RC_PROMPT_NOTE = "Fetched the run brief from run-control"
_RC_ATTACH_NOTE = "Downloaded a mission attachment"
_RC_INTEL_NOTE = "Requested a intel from run-control"
_RC_ARTIFACTS_NOTE = "Submitted an artifact to run-control"
_RC_DONE_NOTE = "Marked the run done"
_AGENT_CONNECTED_NOTE = "Agent connected to XORCISE"

# Used ONLY to enrich the join note with the assigned tailnet address — never to gate activation
# (that's the join-confirmed fact) and never to set event_id (infra is the deterministic plane).
# The join banner is emitted by XORCISE's own join.py ("...joined tailnet as <ip>..."). Capture ONLY
# a real IPv4: the join SCRIPT SOURCE echoes the literal, unexpanded "...as $IP", and the trace
# often surfaces that command/source span before the runtime OUTPUT span — a greedy `(\S+)` captured
# "$IP" into the note (operator saw "joined the tailnet as $IP"). An IPv4-only capture ignores it.
_JOIN_ADDR_RE = re.compile(r"joined tailnet as (\d{1,3}(?:\.\d{1,3}){3})", re.IGNORECASE)

# (anchor, update): the update plus the server-clock time it happened (None → sort last).
_Anchored = tuple[datetime | None, _UpdateInput]


def _utc(dt: datetime | None) -> datetime | None:
    """Defensive UTC normalization: anchors mix SQLite-naive (facts/submissions `created_at`) and
    OTel-aware (trace-ingest receipt) clocks; naive values are UTC by contract. Normalizing here
    keeps this pure function total — `min()` over mixed-source anchors must never TypeError."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


def _valid_ipv4(s: str) -> bool:
    """A well-formed dotted-quad with every octet in 0-255 — so a shape-matching but out-of-range
    token (e.g. 999.1.2.3) is rejected rather than surfaced as an address."""
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and int(p) <= 255 for p in parts)


def _join_address(events: Sequence[AgentEvent]) -> str | None:
    """The assigned tailnet IPv4 from the join banner, scanning ALL events and ALL matches for the
    first VALID dotted-quad (the real runtime-output span may follow the script-source span, and a
    malformed token must not shadow a real one). Returns None when no clean IPv4 is recovered — the
    caller then keeps the plain join label (graceful fallback), never a placeholder or garbage."""
    for e in events:
        for m in _JOIN_ADDR_RE.finditer(e.body or ""):
            if _valid_ipv4(m.group(1)):
                return m.group(1)
    return None


def _fact(facts: Sequence[ObservedFact], kind: str, name: str, value: str) -> ObservedFact | None:
    """The first fact matching (kind, name, value), else None."""
    return next((f for f in facts if f.kind == kind and f.name == name and f.value == value), None)


def infra_updates(
    facts: Sequence[ObservedFact],
    submissions: Sequence[tuple[str, datetime | None]],
    telemetry_ts: datetime | None = None,
    events: Sequence[AgentEvent] = (),
) -> list[_Anchored]:
    """First-party evidence -> deterministic v2 infra `(anchor, _UpdateInput)`s (total).

    `submissions` is the run's ordered `(kind, created_at)` pairs (flag | artifact | intel |
    complete) — plain data, never the runcontrol store. `telemetry_ts` is the first-span
    trace-ingest receipt time (None ⇔ no telemetry yet). `events` (optional) is used ONLY to enrich
    the join note with the assigned tailnet address — NEVER to set `event_id`.

    Every update carries `event_id=None` (the DETERMINISTIC plane) and is paired with its
    server-clock anchor so `terrain_timeline.order_updates` can interleave it chronologically with
    the mission plane. A single edge (`m:agent-rc`) is emitted once per run-control interaction,
    anchored to that interaction's own `created_at`, so the fold surfaces the correct latest action
    at any time-travel index."""
    out: list[_Anchored] = []

    # Anchors are normalized to UTC-aware up front (see _utc): the stores normalize at their own
    # boundary, but this pure function must stay total for any caller's mix of naive/aware inputs.
    telemetry_ts = _utc(telemetry_ts)

    join_fact = _fact(facts, "network-lifecycle", "join", "confirmed")
    join_confirmed = join_fact is not None
    join_ts = _utc(join_fact.created_at) if join_fact is not None else None

    prompt_fact = _fact(facts, "runcontrol-lifecycle", "prompt", "fetched")
    prompt_ts = _utc(prompt_fact.created_at) if prompt_fact is not None else None

    attach_fact = _fact(facts, "runcontrol-lifecycle", "attachment", "fetched")
    attach_ts = _utc(attach_fact.created_at) if attach_fact is not None else None

    def _add(anchor: datetime | None, **kw: object) -> None:
        out.append((anchor, _UpdateInput(**kw)))  # type: ignore[arg-type]

    # The agent workspace node lights up (yellow) the moment the agent has reached XORCISE — either
    # its Headscale node is online (join confirmed) or the OTel collector is receiving its spans. It
    # anchors to the EARLIEST of those signals.
    if join_confirmed or telemetry_ts is not None:
        agent_ts = min((t for t in (join_ts, telemetry_ts) if t is not None), default=None)
        _add(
            agent_ts,
            target_kind="node",
            target_id=_AGENT_NODE,
            state="discovered",
            note=_AGENT_CONNECTED_NOTE,
        )

    # Keys off the real join-confirmed fact (written by the join reconciler once the agent's
    # Headscale node is online), not the setup-time created fact. The trace (when present) only
    # supplies the assigned tailnet IPv4 for the note — it does NOT set event_id.
    if join_confirmed:
        addr = _join_address(events)
        join_note = f"{_HS_JOIN_NOTE} as {addr}" if addr else _HS_JOIN_NOTE
        _add(
            join_ts, target_kind="node", target_id=_HS_JOIN_NODE, state="discovered", note=join_note
        )
        _add(join_ts, target_kind="edge", target_id=_AGENT_HS_EDGE, active=True, note=join_note)

    # Run-control interactions light their rc:* node AND the agent<->run-control edge, each anchored
    # to the driving record's created_at. rc:* endpoints collapse onto the single Run-control node,
    # so the NODE note is never visible; the EDGE note surfaces the LATEST run-control action at any
    # fold position (brief -> attachment/intel -> artifact -> done).
    def _rc(anchor: datetime | None, node_id: str, note: str) -> None:
        _add(anchor, target_kind="node", target_id=node_id, state="discovered", note=note)
        _add(anchor, target_kind="edge", target_id=_AGENT_RC_EDGE, active=True, note=note)

    # Reading the mission brief / fetching an attachment is gated on the first-party fact recorded
    # by the run-control router (GET /mission, GET /attachments) — NOT a body marker the mission
    # content could forge.
    if prompt_fact is not None:
        _rc(prompt_ts, _RC_PROMPT_NODE, _RC_PROMPT_NOTE)
    if attach_fact is not None:
        _rc(attach_ts, _RC_ATTACHMENTS_NODE, _RC_ATTACH_NOTE)
    for kind, created_at in submissions:
        if kind in _ARTIFACT_KINDS:
            _rc(_utc(created_at), _RC_ARTIFACTS_NODE, _RC_ARTIFACTS_NOTE)
        elif kind == "intel":
            _rc(_utc(created_at), _RC_INTEL_NODE, _RC_INTEL_NOTE)
        elif kind == "complete":
            _rc(_utc(created_at), _RC_DONE_NODE, _RC_DONE_NOTE)

    if telemetry_ts is not None:
        _add(
            telemetry_ts,
            target_kind="node",
            target_id=_COLLECTOR_NODE,
            state="discovered",
            note=_COLLECTOR_NOTE,
        )
        _add(
            telemetry_ts,
            target_kind="edge",
            target_id=_AGENT_COLLECTOR_EDGE,
            active=True,
            note=_COLLECTOR_NOTE,
        )
    return out
