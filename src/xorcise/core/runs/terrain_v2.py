"""v2 terrain projector — a PURE, TOTAL function from a run's mission manifest to a
ResolvedTerrainV2. Draws the base graph only: a fixed infra scaffold (agent,
Headscale connection points, run-control endpoints) + ONE mission-graph branch — the
authored graph (TerrainSpec.groups/nodes/edges) when either is non-empty, else a
`static_ips` fallback derived from `environment.static_ips` (service -> network -> octet):
one `segment` group per network, one `host` node per service, no edges, no objective. No
attribution, no fold, no updates — every element carries its base/default state (nodes
`defined`, groups `discovered=False`, edges `active=False`); folding onto that base is
client-side, a later phase.

Takes the manifest as DATA (never imports a sibling module, store, or model — the
dependency rule; stdlib + xorcise.core.contracts.* only). Deterministic; a
missing/empty/malformed manifest block yields fewer elements, never an exception — a
non-dict / id-less authored element is skipped rather than raising.

Two hardening rules cover both mission-graph branches: (1) the fixed
infra scaffold's ids are a reserved namespace (`_RESERVED_IDS`) — an authored or
static_ips-derived group/node/edge whose id collides with one is skipped, so the infra
element always wins and no duplicate element is ever produced; (2) referential validation
— an authored node whose `parent` isn't a known group, or an authored edge whose `src`/`dst`
isn't a known node-or-group, is dropped (drops cascade: an edge to an already-dropped node
drops too). `known_element_ids` exposes the resulting closed vocabulary of ids so
`parse_verdicts_v2` (terrain_attribution_v2) can validate its update targets against exactly
what this projector emitted.
"""

from __future__ import annotations

from xorcise.core.contracts.mission import MissionManifest, TerrainSpec
from xorcise.core.contracts.terrain import (
    ResolvedTerrainV2,
    TerrainEdgeV2,
    TerrainGroup,
    TerrainNodeV2,
)

# Two always-present operator-side groups (orders 0, 1); authored segments continue after.
_AGENT_GROUP = "agent"  # the agent's own sandbox / workspace
_INFRA_GROUP = "xorcise"  # XORCISE platform infra: Headscale (control plane) + run-control
_INFRA_MAX_ORDER = 1

# Fixed infra node ids (used by _infra_scaffold below, and to seed _RESERVED_IDS).
_AGENT_NODE = "agent"
_HS_NODE = "hs"
_RC_NODE = "rc"
_COLLECTOR_NODE = "collector"

# Fixed run infra — known for EVERY run regardless of mission (no authoring).
_HS_POINTS = (
    ("hs:register", "register / login"),
    ("hs:join", "join bundle"),
    ("hs:derp", "DERP relay"),
)
_RC_ENDPOINTS = (
    ("rc:prompt", "prompt / run script"),
    ("rc:attachments", "get_attachment"),
    ("rc:artifacts", "submit artifacts"),
    ("rc:intel", "intel"),
    ("rc:done", "mark done / status"),
)

# The reserved infra-id namespace (M2): every id the fixed scaffold emits, across groups AND
# nodes. Built FROM the scaffold constants above (never duplicated as string literals) so this
# can't drift out of sync with `_infra_scaffold`. An authored or static_ips-derived group/node/
# edge whose id collides with any of these is skipped — the infra element always wins, and no
# duplicate element is ever produced.
_RESERVED_IDS: frozenset[str] = frozenset(
    (
        _AGENT_GROUP,
        _INFRA_GROUP,
        _AGENT_NODE,
        _HS_NODE,
        _RC_NODE,
        _COLLECTOR_NODE,
        *(nid for nid, _ in _HS_POINTS),
        *(nid for nid, _ in _RC_ENDPOINTS),
    )
)


def _infra_scaffold() -> tuple[list[TerrainGroup], list[TerrainNodeV2], list[TerrainEdgeV2]]:
    """The fixed operator-side scaffold: the agent in its own group, and the XORCISE platform
    infra (Headscale, run-control, the OTel collector + their sub-endpoints) in a separate
    `xorcise` group — these are platform infra, NOT part of the agent's workspace. All
    nodes `state="defined"`, groups `discovered=False`, no edge is activated."""
    groups = [
        # XORCISE platform infra renders ABOVE the agent workspace (agent is the hub: membership
        # edges fan UP to the infra, mission/attack edges fan DOWN to the segments below).
        TerrainGroup(id=_INFRA_GROUP, label="XORCISE", kind="infra", order=0),
        TerrainGroup(id=_AGENT_GROUP, label="Agent workspace", kind="agent", order=1),
    ]
    nodes: list[TerrainNodeV2] = [
        TerrainNodeV2(id=_AGENT_NODE, label="Agent", group=_AGENT_GROUP, type="agent"),
        TerrainNodeV2(id=_HS_NODE, label="Headscale", group=_INFRA_GROUP, type="control_plane"),
        TerrainNodeV2(id=_RC_NODE, label="Run-control", group=_INFRA_GROUP, type="run_control"),
        TerrainNodeV2(
            id=_COLLECTOR_NODE, label="OTel collector", group=_INFRA_GROUP, type="control_plane"
        ),
    ]
    for nid, label in _HS_POINTS:
        nodes.append(TerrainNodeV2(id=nid, label=label, group=_INFRA_GROUP, type="endpoint"))
    for nid, label in _RC_ENDPOINTS:
        nodes.append(TerrainNodeV2(id=nid, label=label, group=_INFRA_GROUP, type="endpoint"))
    edges = [
        TerrainEdgeV2(id="m:agent-hs", src=_AGENT_NODE, dst=_HS_NODE),
        TerrainEdgeV2(id="m:agent-rc", src=_AGENT_NODE, dst=_RC_NODE),
        TerrainEdgeV2(id="m:agent-collector", src=_AGENT_NODE, dst=_COLLECTOR_NODE),
    ]
    return groups, nodes, edges


def _authored(
    spec: TerrainSpec,
    infra_group_ids: frozenset[str],
    infra_node_ids: frozenset[str],
) -> tuple[list[TerrainGroup], list[TerrainNodeV2], list[TerrainEdgeV2], str | None]:
    """Build the authored mission graph from TerrainSpec's permissive dict tuples. Total: a
    non-dict / id-less element is skipped, never fatal. Two extra guards keep the authored graph
    honest:
    - infra-id namespace: any group/node/edge whose id collides with a reserved infra id
      (`_RESERVED_IDS`) is skipped — the infra element always wins, never a duplicate.
    - referential validation: a node whose `parent` doesn't resolve to a known group (infra
      OR authored-so-far), or an edge whose `src`/`dst` doesn't resolve to a known node-or-group,
      is dropped. This cascades: an edge targeting an already-dropped node is dropped too.
    `objective_id` is the first surviving node with `objective:true`, in authoring order."""
    groups: list[TerrainGroup] = []
    known_group_ids: set[str] = set(infra_group_ids)
    order = _INFRA_MAX_ORDER + 1
    for raw in spec.groups:
        if not isinstance(raw, dict):
            continue
        gid = raw.get("id")
        if not isinstance(gid, str) or not gid:
            continue
        if gid in _RESERVED_IDS:
            continue  # M2: collides with a reserved infra id -> skip, infra element wins
        label_val = raw.get("label")
        label = label_val if isinstance(label_val, str) and label_val else gid
        desc_val = raw.get("description")
        description = desc_val if isinstance(desc_val, str) else None
        disc_cond_val = raw.get("discovery_condition")
        discovery_condition = disc_cond_val if isinstance(disc_cond_val, str) else None
        groups.append(
            TerrainGroup(
                id=gid,
                label=label,
                description=description,
                discovery_condition=discovery_condition,
                kind="segment",
                order=order,
                hidden=bool(raw.get("hidden", False)),
            )
        )
        known_group_ids.add(gid)
        order += 1

    nodes: list[TerrainNodeV2] = []
    known_node_ids: set[str] = set(infra_node_ids)
    objective_id: str | None = None
    for raw in spec.nodes:
        if not isinstance(raw, dict):
            continue
        nid = raw.get("id")
        if not isinstance(nid, str) or not nid:
            continue
        if nid in _RESERVED_IDS:
            continue  # M2: collides with a reserved infra id -> skip, infra element wins
        group_val = raw.get("parent")
        if not isinstance(group_val, str) or not group_val:
            continue  # TerrainNodeV2.group is required; an unparented node can't be placed
        if group_val not in known_group_ids:
            continue  # M3: dangling parent reference -> drop rather than raise
        type_val = raw.get("type")
        ntype = type_val if isinstance(type_val, str) and type_val else "service"
        desc_val = raw.get("description")
        description = desc_val if isinstance(desc_val, str) else None
        disc_cond_val = raw.get("discovery_condition")
        discovery_condition = disc_cond_val if isinstance(disc_cond_val, str) else None
        comp_cond_val = raw.get("completion_condition")
        completion_condition = comp_cond_val if isinstance(comp_cond_val, str) else None
        label_val = raw.get("label")
        label = label_val if isinstance(label_val, str) and label_val else nid
        objective = bool(raw.get("objective", False))
        nodes.append(
            TerrainNodeV2(
                id=nid,
                label=label,
                group=group_val,
                type=ntype,
                objective=objective,
                description=description,
                discovery_condition=discovery_condition,
                completion_condition=completion_condition,
            )
        )
        known_node_ids.add(nid)
        if objective and objective_id is None:
            objective_id = nid

    # Edge endpoints may be any known node or group — authored OR infra (both `known_*` sets are
    # seeded with the infra scaffold ids above). This lets a mission author an edge from the
    # `agent` hub into an entry node (e.g. agent -> web: "the agent reaches this externally-
    # listening service"), bridging the infra and mission planes.
    known_node_or_group_ids = known_node_ids | known_group_ids
    edges: list[TerrainEdgeV2] = []
    for raw in spec.edges:
        if not isinstance(raw, dict):
            continue
        eid = raw.get("id")
        if not isinstance(eid, str) or not eid:
            continue
        if eid in _RESERVED_IDS:
            continue  # M2: collides with a reserved infra id -> skip, infra element wins
        src_val = raw.get("src")
        dst_val = raw.get("dst")
        if not isinstance(src_val, str) or not src_val:
            continue
        if not isinstance(dst_val, str) or not dst_val:
            continue
        if src_val not in known_node_or_group_ids or dst_val not in known_node_or_group_ids:
            continue  # M3: dangling endpoint (incl. one dropped above) -> drop rather than raise
        edge_label_val = raw.get("label")
        edge_label = edge_label_val if isinstance(edge_label_val, str) else None
        edges.append(TerrainEdgeV2(id=eid, src=src_val, dst=dst_val, label=edge_label))

    return groups, nodes, edges, objective_id


def _static_ips_fallback(
    static_ips: dict[str, dict[str, int]],
) -> tuple[list[TerrainGroup], list[TerrainNodeV2]]:
    """Derive a minimal mission graph from `EnvironmentSpec.static_ips`
    (service -> network -> last_octet) for a mission with no authored terrain groups/nodes.
    One `TerrainGroup` per network (kind `segment`, in first-seen order), one `TerrainNodeV2`
    per service (`group` = that service's first-listed SURVIVING network, `type="host"`, no
    objective), and no edges — the raw static_ips shape carries no relationship data to derive
    edges from. Total: a service with no (surviving) network is skipped (unplaceable) rather than
    raising; an empty `static_ips` yields no groups/nodes. M2: a network or service id that
    collides with a reserved infra id is skipped (the infra element wins, never a duplicate) —
    a service left with no surviving network to attach to is dropped too (no dangling parent)."""
    groups: list[TerrainGroup] = []
    seen_networks: set[str] = set()
    order = _INFRA_MAX_ORDER + 1
    for networks in static_ips.values():
        for network in networks:
            if network in seen_networks:
                continue
            seen_networks.add(network)
            if network in _RESERVED_IDS:
                continue  # M2: collides with a reserved infra id -> skip, infra element wins
            groups.append(TerrainGroup(id=network, label=network, kind="segment", order=order))
            order += 1

    known_group_ids = {g.id for g in groups}
    nodes: list[TerrainNodeV2] = []
    for service, networks in static_ips.items():
        if service in _RESERVED_IDS:
            continue  # M2: collides with a reserved infra id -> skip, infra element wins
        first_network = next((n for n in networks if n in known_group_ids), None)
        if first_network is None:
            continue  # no surviving network to place this service's node under
        nodes.append(TerrainNodeV2(id=service, label=service, group=first_network, type="host"))
    return groups, nodes


def project_terrain_v2(
    run_id: str, mission_id: str, manifest: MissionManifest | None
) -> ResolvedTerrainV2:
    """Project the v2 base terrain: a PURE, TOTAL function from run_id, mission_id, and an
    optional manifest to a ResolvedTerrainV2 graph. Always emits the fixed infra scaffold, then
    ONE mission-graph branch: the authored groups/nodes/edges block from `manifest.terrain`
    when either is non-empty (authored wins), else a graph derived from
    `manifest.environment.static_ips`. No manifest, or a manifest whose terrain and static_ips
    are both empty, degrades to just the infra scaffold. No attribution, no fold, no updates
    this phase."""
    groups, nodes, edges = _infra_scaffold()
    infra_group_ids = frozenset(g.id for g in groups)
    infra_node_ids = frozenset(n.id for n in nodes)
    summary: str | None = None
    objective_id: str | None = None
    if manifest is not None:
        spec = manifest.terrain
        if spec is not None:
            summary = spec.summary
        if spec is not None and (spec.groups or spec.nodes):
            ag, an, ae, obj = _authored(spec, infra_group_ids, infra_node_ids)
            groups = groups + ag
            nodes = nodes + an
            edges = edges + ae
            objective_id = obj
        else:
            # A static mission has no environment/static_ips → empty fallback (no service nodes).
            static_ips = manifest.environment.static_ips if manifest.environment else {}
            fg, fn = _static_ips_fallback(static_ips)
            groups = groups + fg
            nodes = nodes + fn
    return ResolvedTerrainV2(
        run_id=run_id,
        mission_id=mission_id,
        summary=summary,
        groups=tuple(groups),
        nodes=tuple(nodes),
        edges=tuple(edges),
        updates=(),
        attribution=None,
        objective_id=objective_id,
    )


def known_element_ids(resolved: ResolvedTerrainV2) -> set[str]:
    """The closed vocabulary of every element id in a resolved v2 graph — the union of group
    ids, node ids, and edge ids (infra scaffold ids included). Pure, total. This is the
    `known_ids` set `parse_verdicts_v2` (terrain_attribution_v2) validates update targets
    against, so an attributed update can only ever reference something the projector actually
    emitted."""
    return (
        {g.id for g in resolved.groups}
        | {n.id for n in resolved.nodes}
        | {e.id for e in resolved.edges}
    )


def _infra_ids() -> frozenset[str]:
    """Every id the fixed infra scaffold emits — groups, nodes, AND edges. Unlike `_RESERVED_IDS`
    (which only covers the group/node collision guard used by `_authored`/`_static_ips_fallback`,
    M2, and deliberately omits infra edge ids since authored edges can't collide with the
    `m:`-prefixed scaffold edges), this is the FULL infra element namespace — what the mission
    plane must never see or target. Derived fresh from `_infra_scaffold()` so it can't drift out
    of sync with the scaffold it describes."""
    groups, nodes, edges = _infra_scaffold()
    return frozenset({g.id for g in groups} | {n.id for n in nodes} | {e.id for e in edges})


def mission_terrain(
    resolved: ResolvedTerrainV2,
) -> tuple[tuple[TerrainGroup, ...], tuple[TerrainNodeV2, ...], tuple[TerrainEdgeV2, ...]]:
    """The AUTHORED (mission-plane) subset of a resolved v2 graph — every group/node/edge EXCEPT
    the fixed infra scaffold (the agent workspace + XORCISE platform infra, including their
    edges). Pure, total. The mission-attribution prompt (`PromptContext` in
    `terrain_attribution_v2`) must be built from this subset, never the full resolved graph: the
    infra plane is deterministic and the sole writer of infra element updates (`infra_updates`),
    so the BYOM model must never even see an infra id, let alone be allowed to target one."""
    infra_ids = _infra_ids()
    groups = tuple(g for g in resolved.groups if g.id not in infra_ids)
    nodes = tuple(n for n in resolved.nodes if n.id not in infra_ids)
    edges = tuple(e for e in resolved.edges if e.id not in infra_ids)
    return groups, nodes, edges


def mission_element_ids(resolved: ResolvedTerrainV2) -> set[str]:
    """The closed vocabulary of AUTHORED (mission-plane) element ids only — `known_element_ids`
    minus the fixed infra scaffold's ids (groups, nodes, AND edges). Pure, total. This is the
    `known_ids` set the mission-attribution model's update-only guard must validate targets
    against, so a verdict can never target an infra id (`hs:join`, `m:agent-hs`, `collector`, …) —
    only the deterministic infra plane may write infra element updates."""
    return known_element_ids(resolved) - _infra_ids()
