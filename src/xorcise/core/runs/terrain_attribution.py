"""Shared attributable-event-ids helper (runs module) — the set of agent_event kinds that carry a
terrain action, used by the v2 terrain route (`rest/routers/runs.py::run_terrain_v2`) and the v2
BYOM attribution plane (`terrain_attribution_v2.py`) as the denominator for attribution progress.
"""

from __future__ import annotations

from collections.abc import Sequence

from xorcise.core.contracts.agent_event import AgentEvent, AgentEventKind

# Action-group kinds only; conversation (message/thinking) + debug (metric/unknown) carry no
# terrain action, so they are never sent to the model.
_ATTRIBUTABLE_KINDS: frozenset[str] = frozenset(k.value for k in AgentEventKind) - {
    "message",
    "thinking",
    "metric",
    "unknown",
}


def attributable_event_ids(events: Sequence[AgentEvent]) -> set[str]:
    """The ids of events the attributor considers — the denominator for attribution progress and
    the set the client marks 'pending' until each appears in the terrain actions."""
    return {e.id for e in events if e.kind.value in _ATTRIBUTABLE_KINDS}
