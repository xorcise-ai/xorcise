"""Resolved terrain wire DTOs (LEAF) — the run-scoped network/infrastructure map the terrain
view renders. Agent-agnostic, display-only, derived + rebuildable; never a grading input.
`_Frozen` is the shared base of every type here; `AttributionStatus` is the shared per-run
BYOM attribution-progress DTO served alongside the v2 resolved map. The v2 types below
(TerrainGroup/TerrainNodeV2/TerrainEdgeV2/TerrainUpdate/ResolvedTerrainV2) are the current
terrain wire contract. Imports stdlib + pydantic only."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AttributionStatus(_Frozen):
    """Per-run BYOM mission-plane attribution progress (iteration 2), for subtle per-span
    feedback in the Trace view. `running` is true while a catch-up batch is in flight for this run
    (spans not yet attributed can show an "attributing…" pulse); `attributed`/`attributable` are the
    counts of action-events the model has considered vs the total attributable events, for a
    run-level "N/M" read. A span is "completed" when its event_id appears in the v2 `updates`
    stream; "pending" when it is attributable but absent — the client derives that from `updates`
    + the event list."""

    running: bool = False
    attributed: int = 0
    attributable: int = 0
    # Every event the model has recorded a verdict for — applicable OR inapplicable. The v2
    # `updates` stream carries only the applicable ones (they have an edge / are click-to-replay
    # targets), so this is how the client tells "analyzed → no terrain action" (a considered id
    # absent from `updates`) apart from "not yet analyzed" (absent here too). Without it, a
    # rejected span reads as still-pending.
    considered_event_ids: tuple[str, ...] = Field(default_factory=tuple)


# v2 resolved contract types (iteration 2+)


class TerrainGroup(_Frozen):
    """A logical grouping of nodes on the v2 map. Groups organize the infrastructure
    and can be hidden during discovery."""

    id: str
    label: str
    description: str | None = None
    discovery_condition: str | None = None  # authored — the model matches spans against this
    kind: Literal["infra", "agent", "segment"] = "segment"
    order: int = 0
    hidden: bool = False
    discovered: bool = False


class TerrainNodeV2(_Frozen):
    """One node on the v2 map. Carries base state only; dynamic state folding is client-side."""

    id: str
    label: str
    group: str  # TerrainGroup.id
    type: str = "service"
    objective: bool = False
    description: str | None = None
    discovery_condition: str | None = None  # authored — the model matches spans against this
    completion_condition: str | None = None  # authored — the model matches spans against this
    state: Literal["defined", "discovered", "completed"] = "defined"


class TerrainEdgeV2(_Frozen):
    """A link between two nodes on the v2 map. Carries base state only."""

    id: str
    src: str  # TerrainNodeV2.id
    dst: str  # TerrainNodeV2.id
    label: str | None = None
    active: bool = False


class TerrainUpdate(_Frozen):
    """An update event in the v2 resolved terrain stream. Folds onto nodes/groups/edges
    to represent discovered/completed state transitions over time. `note` is an optional
    short free-text description of what the agent did in the source span (display-only)."""

    seq: int
    target_kind: Literal["node", "group", "edge"]
    target_id: str
    event_id: str | None = None
    state: Literal["discovered", "completed"] | None = None
    discovered: bool | None = None
    active: bool | None = None
    note: str | None = None
    # Server-clock receipt anchor (the same clock `order_updates` sorts by): a mission update's
    # span trace-ingest receipt time, an infra update's driving-record time, or None when anchorless
    # (objective grade / missing timestamp). Lets the frontend interleave infra rows into the Trace
    # chronologically and time-travel to them. Display/ordering only — never a grading input.
    ts: datetime | None = None


class ResolvedTerrainV2(_Frozen):
    """The v2 resolved terrain wire DTO — the run-scoped network/infrastructure map
    with groups, nodes, edges, and an updates stream for state transitions."""

    run_id: str
    mission_id: str
    summary: str | None = None
    groups: tuple[TerrainGroup, ...] = Field(default_factory=tuple)
    nodes: tuple[TerrainNodeV2, ...] = Field(default_factory=tuple)
    edges: tuple[TerrainEdgeV2, ...] = Field(default_factory=tuple)
    updates: tuple[TerrainUpdate, ...] = Field(default_factory=tuple)
    attribution: AttributionStatus | None = None
    objective_id: str | None = None
