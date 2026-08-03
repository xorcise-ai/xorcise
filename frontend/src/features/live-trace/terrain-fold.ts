import type { ResolvedTerrainV2, TerrainEdgeV2, TerrainGroup, TerrainNodeV2, TerrainUpdate } from "@/lib/api/types";

/**
 * Pure client-side fold over a v2 terrain's `updates` array.
 *
 * The terrain server only ever emits the base graph + an ORDERED updates array; all state
 * folding happens here, on the frontend. This is what makes time-travel possible: rewinding
 * a run to any point just means folding a shorter prefix of the same array.
 *
 * CRITICAL: fold by ARRAY POSITION, never by `TerrainUpdate.seq`. Infra-plane updates carry
 * synthetic seq 0..n-1 while mission-plane updates keep their DB autoincrement seq -- the two
 * planes' seq values are NOT a global order. The served array order is authoritative.
 */

export type NodeState = "defined" | "discovered" | "completed";

export interface FoldedNode {
  node: TerrainNodeV2;
  state: NodeState;
}

export interface FoldedGroup {
  group: TerrainGroup;
  discovered: boolean;
}

export interface FoldedEdge {
  edge: TerrainEdgeV2;
  active: boolean;
}

export interface FoldedTerrain {
  groups: FoldedGroup[];
  nodes: FoldedNode[];
  edges: FoldedEdge[];
  /** element ids the update(s) at the fold boundary touched -- the delta to pulse. */
  pulse: { nodes: Set<string>; groups: Set<string>; edges: Set<string> };
}

const NODE_STATE_RANK: Record<NodeState, number> = {
  defined: 0,
  discovered: 1,
  completed: 2,
};

function emptyPulse(): FoldedTerrain["pulse"] {
  return { nodes: new Set(), groups: new Set(), edges: new Set() };
}

/**
 * Fold `updates[0..k]` (INCLUSIVE, ARRAY INDEX) onto the base graph.
 *
 * `k = -1` => base (nothing folded); `k >= updates.length - 1` => latest. Advance is monotonic:
 * node state only climbs `defined < discovered < completed` and never regresses; `group.discovered`
 * and `edge.active` latch true once set. Updates targeting an unknown id are ignored. The optional
 * `pulseIds` (typically from `pulseIdsForIndex`) is partitioned by kind into the returned `pulse`.
 */
export function foldTerrain(t: ResolvedTerrainV2, k: number, pulseIds?: Set<string> | null): FoldedTerrain {
  const baseNodes = t.nodes ?? [];
  const baseGroups = t.groups ?? [];
  const baseEdges = t.edges ?? [];
  const updates = t.updates ?? [];

  const nodeState = new Map<string, NodeState>();
  const groupDiscovered = new Map<string, boolean>();
  const edgeActive = new Map<string, boolean>();

  for (const node of baseNodes) {
    nodeState.set(node.id, node.state);
  }
  for (const group of baseGroups) {
    groupDiscovered.set(group.id, group.discovered);
  }
  for (const edge of baseEdges) {
    edgeActive.set(edge.id, edge.active);
  }

  const upperBound = Math.min(k, updates.length - 1);
  for (let i = 0; i <= upperBound; i++) {
    applyUpdate(updates[i], nodeState, groupDiscovered, edgeActive);
  }

  // Deterministic entry-edge backstop: the attributor records the agent REACHING a node as node
  // discovery but routinely omits the agent->node arrival edge (observed: e-agent-web never lit
  // while `web` was discovered). When a node has been reached (discovered/completed) and its ONLY
  // authored inbound edge is from an agent node, the agent's connection into it was necessarily
  // traversed -> activate that edge. Keyed off `nodeState` at the current fold `k`, so it respects
  // time-travel; idempotent with the model activating the edge directly.
  const agentNodeIds = new Set(baseNodes.filter((n) => n.type === "agent").map((n) => n.id));
  if (agentNodeIds.size > 0) {
    const inboundByDst = new Map<string, TerrainEdgeV2[]>();
    for (const e of baseEdges) {
      const list = inboundByDst.get(e.dst);
      if (list) list.push(e);
      else inboundByDst.set(e.dst, [e]);
    }
    for (const e of baseEdges) {
      if (!agentNodeIds.has(e.src)) continue;
      const dstState = nodeState.get(e.dst);
      if (dstState !== "discovered" && dstState !== "completed") continue;
      const inbound = inboundByDst.get(e.dst) ?? [];
      if (inbound.every((x) => agentNodeIds.has(x.src))) {
        edgeActive.set(e.id, true);
      }
    }
  }

  const nodes: FoldedNode[] = baseNodes.map((node) => ({
    node,
    state: nodeState.get(node.id) ?? node.state,
  }));
  const groups: FoldedGroup[] = baseGroups.map((group) => ({
    group,
    discovered: groupDiscovered.get(group.id) ?? group.discovered,
  }));
  const edges: FoldedEdge[] = baseEdges.map((edge) => ({
    edge,
    active: edgeActive.get(edge.id) ?? edge.active,
  }));

  const pulse = emptyPulse();
  if (pulseIds) {
    const nodeIds = new Set(baseNodes.map((n) => n.id));
    const groupIds = new Set(baseGroups.map((g) => g.id));
    const edgeIds = new Set(baseEdges.map((e) => e.id));
    for (const id of pulseIds) {
      if (nodeIds.has(id)) pulse.nodes.add(id);
      if (groupIds.has(id)) pulse.groups.add(id);
      if (edgeIds.has(id)) pulse.edges.add(id);
    }
  }

  return { groups, nodes, edges, pulse };
}

function applyUpdate(
  update: TerrainUpdate,
  nodeState: Map<string, NodeState>,
  groupDiscovered: Map<string, boolean>,
  edgeActive: Map<string, boolean>,
): void {
  switch (update.target_kind) {
    case "node": {
      if (!nodeState.has(update.target_id)) return;
      if (!update.state) return;
      const current = nodeState.get(update.target_id)!;
      if (NODE_STATE_RANK[update.state] > NODE_STATE_RANK[current]) {
        nodeState.set(update.target_id, update.state);
      }
      return;
    }
    case "group": {
      if (!groupDiscovered.has(update.target_id)) return;
      if (update.discovered) {
        groupDiscovered.set(update.target_id, true);
      }
      return;
    }
    case "edge": {
      if (!edgeActive.has(update.target_id)) return;
      if (update.active) {
        edgeActive.set(update.target_id, true);
      }
      return;
    }
  }
}

/**
 * The set of edge ids on the path(s) from each `seedNodeIds` node back to an agent node, walking
 * INBOUND edges (dst → src) transitively. Used to light the whole probe path — not just the last
 * edge — yellow: when a downstream node is being probed, every edge the agent traversed to reach it
 * (e.g. agent→web→internal) lights up. Stops at agent nodes; a `visited` guard terminates cycles.
 */
export function probingPathEdgeIds(
  seedNodeIds: ReadonlySet<string>,
  edges: readonly { id: string; src: string; dst: string }[],
  agentNodeIds: ReadonlySet<string>,
): Set<string> {
  const inboundByDst = new Map<string, { id: string; src: string; dst: string }[]>();
  for (const e of edges) {
    const list = inboundByDst.get(e.dst);
    if (list) list.push(e);
    else inboundByDst.set(e.dst, [e]);
  }
  const out = new Set<string>();
  const visited = new Set<string>();
  const stack = [...seedNodeIds];
  while (stack.length) {
    const node = stack.pop()!;
    if (visited.has(node) || agentNodeIds.has(node)) continue; // reached the agent (or a cycle)
    visited.add(node);
    for (const e of inboundByDst.get(node) ?? []) {
      out.add(e.id);
      if (!visited.has(e.src)) stack.push(e.src);
    }
  }
  return out;
}

/** The span BASE of an event id — everything before the last `:` (the `:tool`/`:out` sub-event
 *  suffix). A span's command and output share this base. Mirrors the trace timeline's `idBase`. */
function spanBase(id: string): string {
  const i = id.lastIndexOf(":");
  return i === -1 ? id : id.slice(0, i);
}

/**
 * The array index to fold to for a clicked span: the LAST index whose update belongs to the SAME
 * span as `eventId`, or `null` if that span produced no update (never acted on the map).
 *
 * Matches on the span BASE, not the exact id: the trace selects the terminal COMMAND (`…:tool`),
 * but the attributor usually keyed the update on the OUTPUT (`…:out`). An exact match missed those,
 * so `k` fell back to the latest fold and the map showed the fully-completed (green) state instead
 * of rewinding — the time-travel bug. Base-matching locates the span's update regardless of which
 * sub-event it landed on.
 */
export function foldIndexForEvent(updates: readonly TerrainUpdate[], eventId: string): number | null {
  // Infra selection ids (`infra:<seq>`, minted by deriveInfraRows) carry the fold index directly:
  // `seq` IS the update's array position (order_updates re-seqs to array index), so an infra row
  // rewinds straight to that moment — no span/event_id involved (the infra plane stays event_id=null).
  const infra = /^infra:(\d+)$/.exec(eventId);
  if (infra) {
    const seq = Number(infra[1]);
    return seq >= 0 && seq < updates.length ? seq : null;
  }
  const target = spanBase(eventId);
  let found: number | null = null;
  for (let i = 0; i < updates.length; i++) {
    const eid = updates[i].event_id;
    if (eid && spanBase(eid) === target) {
      found = i;
    }
  }
  return found;
}

/** True for a fixed infra-scaffold element id (agent / OTel collector / Headscale / run-control /
 *  the agent↔* edges). Excludes mission nodes, so the objective-grade update — also event_id=null
 *  — is NOT mistaken for an infra activity. */
export function isInfraTarget(id: string): boolean {
  return (
    id === "agent" ||
    id === "collector" ||
    id.startsWith("hs:") ||
    id.startsWith("rc:") ||
    id.startsWith("m:agent-")
  );
}

/** One clickable Trace row for an infra activity (join, brief, artifact, telemetry, …). */
export interface InfraRow {
  /** Synthetic selection id (`infra:<seq>`) resolved by `foldIndexForEvent` to a fold index. */
  id: string;
  /** Server-clock receipt anchor (ISO string) or null when anchorless. */
  ts: string | null;
  /** Operator-facing label (the update note). */
  label: string;
  /** A representative infra target id (drives the row's icon). */
  targetId: string;
  /** The fold index this row time-travels to (the activity's LAST update). */
  seq: number;
}

/**
 * Derive one clickable Trace row per infra ACTIVITY from the deterministic infra-plane updates
 * (`event_id == null && isInfraTarget`). Updates that share a `ts` are the same activity (join lights
 * a node + an edge at one instant) → collapsed to a single row whose fold target is the activity's
 * LAST update (max seq), so time-travel includes the whole activity. Rows are ordered by seq (the
 * served array is already receipt-time ordered). An anchorless infra update (ts null) still yields a
 * row, keyed by its own seq so it can't merge with unrelated activities.
 */
export function deriveInfraRows(updates: readonly TerrainUpdate[]): InfraRow[] {
  interface Acc {
    seq: number;
    ts: string | null;
    label: string;
    targetId: string;
  }
  const byKey = new Map<string, Acc>();
  for (const u of updates) {
    if (u.event_id != null || !isInfraTarget(u.target_id)) continue;
    const key = u.ts ?? `@${u.seq}`;
    const acc = byKey.get(key);
    if (!acc) {
      byKey.set(key, { seq: u.seq, ts: u.ts ?? null, label: u.note ?? "", targetId: u.target_id });
    } else {
      if (u.seq > acc.seq) acc.seq = u.seq;
      if (!acc.label && u.note) acc.label = u.note;
      // prefer a node target for the row icon (edges are m:agent-*)
      if (acc.targetId.startsWith("m:agent-") && !u.target_id.startsWith("m:agent-")) {
        acc.targetId = u.target_id;
      }
    }
  }
  return [...byKey.values()]
    .sort((a, b) => a.seq - b.seq)
    .map((a) => ({
      id: `infra:${a.seq}`,
      ts: a.ts,
      label: a.label || a.targetId,
      targetId: a.targetId,
      seq: a.seq,
    }));
}

/**
 * The set of element ids touched by ALL updates sharing the boundary update's `event_id`, so a
 * multi-update span pulses every element it changed, not just the last. `event_id === null` (infra)
 * => just the single update at `k`. Out-of-range `k` => empty set.
 */
export function pulseIdsForIndex(updates: readonly TerrainUpdate[], k: number): Set<string> {
  if (k < 0 || k >= updates.length) return new Set();
  const boundary = updates[k];
  if (boundary.event_id == null) {
    // Infra plane: an activity is the set of updates sharing one receipt `ts` (join = node + edge),
    // so pulse them all — selecting the join row lights both the Headscale node and its edge. With
    // no ts (anchorless), fall back to the single boundary target.
    if (boundary.ts == null) return new Set([boundary.target_id]);
    const ids = new Set<string>();
    for (const u of updates) {
      if (u.event_id == null && u.ts === boundary.ts) ids.add(u.target_id);
    }
    return ids;
  }
  const ids = new Set<string>();
  for (const update of updates) {
    if (update.event_id === boundary.event_id) {
      ids.add(update.target_id);
    }
  }
  return ids;
}
