import type { FoldedEdge, FoldedGroup, FoldedNode, FoldedTerrain } from "./terrain-fold";

/**
 * Deterministic spatial layout for the v2 terrain map.
 *
 * The terrain is projector-GENERATED (nodes have no authored coordinates), so we compute a
 * stable layout: authored GROUPS stack as full-width horizontal bands, ordered by `order`
 * (low = top). ALL groups are laid out, hidden or not — a hidden group that has not yet been
 * discovered renders GREYED-BUT-VISIBLE (fog of war), never omitted; the renderer draws it as a
 * dashed/grey box and solidifies it once discovered, on the next fold.
 *
 * Each group's member NODES are placed evenly across its band, EXCEPT `type === "endpoint"`
 * nodes (e.g. `hs:register`, `rc:artifacts`) — those clutter the map as colliding child circles,
 * so they are collapsed onto their parent SERVICE (the id prefix before its first `:`, e.g.
 * `hs`, `rc`) and exposed as that service's `routes` for a hover tooltip, rather than drawn as
 * their own circles. An endpoint whose prefix matches no drawn (non-endpoint) node is dropped.
 *
 * EDGES resolve between node/group centres; an edge that targets a collapsed endpoint re-anchors
 * to that endpoint's parent service (if drawn), else the edge is dropped — same as any other
 * edge with a missing/undrawn endpoint.
 *
 * Pure + deterministic — same FoldedTerrain always yields the same pixels.
 */

export interface LaidOutNode {
  fnode: FoldedNode;
  x: number;
  y: number;
  r: number;
  /** The node's horizontal slot width (bandW / node-count in its band). The renderer clamps the
   *  label to this so a long label can't spill sideways under an adjacent node's label. */
  slotW: number;
  /** Endpoint nodes collapsed onto this (service) node, for a hover "routes" tooltip. Absent/empty when this node has none. */
  routes?: { id: string; label: string }[];
}
export interface LaidOutGroup {
  fgroup: FoldedGroup;
  x: number;
  y: number;
  w: number;
  h: number;
}
export interface LaidOutEdge {
  fedge: FoldedEdge;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}
export interface TerrainLayout {
  width: number;
  height: number;
  groups: LaidOutGroup[];
  nodes: LaidOutNode[];
  edges: LaidOutEdge[];
}

const WIDTH = 760;
const PAD = 20;
const ZONE_GAP = 18;
const ZONE_HEADER = 26; // reserved for the group label, above its nodes
const PARENT_R = 16;
const PARENT_ROW = 22; // node-centre offset below the band header
// Room below the node centre for the disc PLUS a clamped two-line label (the renderer wraps a long
// label to at most two lines) — so the second line stays inside the band and never collides with
// the next band's header.
const BOTTOM_PAD = 30;
const ZONE_H = ZONE_HEADER + PARENT_ROW + PARENT_R + BOTTOM_PAD;

/** The parent service id an endpoint collapses onto: the id prefix before its first `:`. */
function endpointParentId(id: string): string {
  const i = id.indexOf(":");
  return i === -1 ? id : id.slice(0, i);
}

const STATE_RANK: Record<string, number> = { defined: 0, discovered: 1, completed: 2 };
/** The higher (more-advanced) of two node states on the defined<discovered<completed lattice. */
function maxState(a: FoldedNode["state"], b: FoldedNode["state"]): FoldedNode["state"] {
  return (STATE_RANK[b] ?? 0) > (STATE_RANK[a] ?? 0) ? b : a;
}

export function layoutTerrainV2(f: FoldedTerrain): TerrainLayout {
  // Every authored group is laid out (hidden/undiscovered groups render greyed, not omitted).
  const orderedGroups = [...(f.groups ?? [])].sort((a, b) => a.group.order - b.group.order);
  const groupIds = new Set(orderedGroups.map((fg) => fg.group.id));

  const allNodes = (f.nodes ?? []).filter((fn) => groupIds.has(fn.node.group));
  const endpointNodes = allNodes.filter((fn) => fn.node.type === "endpoint");
  const drawnNodes = allNodes.filter((fn) => fn.node.type !== "endpoint");
  const drawnNodeIds = new Set(drawnNodes.map((fn) => fn.node.id));

  // Collapse each endpoint onto its parent service (only when that service is actually drawn);
  // an endpoint whose prefix matches no drawn node is dropped entirely (per parent instructions).
  const routesByParent = new Map<string, { id: string; label: string }[]>();
  const endpointToParent = new Map<string, string>();
  // A collapsed endpoint's state must surface on its (drawn) parent service node — otherwise a
  // join/route firing on a now-hidden endpoint (e.g. `hs:join` discovered) would never show on
  // the visible Headscale node. Track the most-advanced collapsed-endpoint state per parent.
  const stateByParent = new Map<string, FoldedNode["state"]>();
  for (const fn of endpointNodes) {
    const parentId = endpointParentId(fn.node.id);
    if (!drawnNodeIds.has(parentId)) continue;
    endpointToParent.set(fn.node.id, parentId);
    const list = routesByParent.get(parentId) ?? [];
    list.push({ id: fn.node.id, label: fn.node.label });
    routesByParent.set(parentId, list);
    stateByParent.set(parentId, maxState(stateByParent.get(parentId) ?? "defined", fn.state));
  }

  const laidGroups: LaidOutGroup[] = [];
  const laidNodes: LaidOutNode[] = [];
  const nodePos = new Map<string, LaidOutNode>();
  const groupBand = new Map<string, LaidOutGroup>();
  const bandW = WIDTH - 2 * PAD;

  let y = PAD;
  for (const fgroup of orderedGroups) {
    const band: LaidOutGroup = { fgroup, x: PAD, y, w: bandW, h: ZONE_H };
    laidGroups.push(band);
    groupBand.set(fgroup.group.id, band);

    const members = drawnNodes.filter((fn) => fn.node.group === fgroup.group.id);
    const n = members.length || 1;
    const slotW = bandW / n;
    members.forEach((fnode, i) => {
      const px = PAD + slotW * (i + 0.5);
      const py = y + ZONE_HEADER + PARENT_ROW;
      const routes = routesByParent.get(fnode.node.id);
      // Surface any collapsed endpoint's state onto the service node so it activates visibly.
      const collapsed = stateByParent.get(fnode.node.id);
      const effective: FoldedNode = collapsed
        ? { ...fnode, state: maxState(fnode.state, collapsed) }
        : fnode;
      const laid: LaidOutNode = {
        fnode: effective,
        x: px,
        y: py,
        r: PARENT_R,
        slotW,
        ...(routes ? { routes } : {}),
      };
      laidNodes.push(laid);
      nodePos.set(fnode.node.id, laid);
    });

    y += ZONE_H + ZONE_GAP;
  }
  const height = orderedGroups.length ? y - ZONE_GAP + PAD : PAD * 2;

  // Resolve an edge endpoint: a drawn node id → its centre; a collapsed endpoint id → its parent
  // service's centre (if that service is drawn); a group id → that group band's top-centre.
  const at = (id: string): { x: number; y: number } | undefined => {
    const nodeHere = nodePos.get(id);
    if (nodeHere) return { x: nodeHere.x, y: nodeHere.y };
    const parentId = endpointToParent.get(id);
    if (parentId) {
      const parentNode = nodePos.get(parentId);
      if (parentNode) return { x: parentNode.x, y: parentNode.y };
    }
    const band = groupBand.get(id);
    if (band) return { x: band.x + band.w / 2, y: band.y };
    return undefined;
  };

  const laidEdges: LaidOutEdge[] = [];
  for (const fedge of f.edges ?? []) {
    const a = at(fedge.edge.src);
    const b = at(fedge.edge.dst);
    if (!a || !b) continue; // missing/undrawn endpoint (e.g. a collapsed endpoint with no drawn parent)
    if (a.x === b.x && a.y === b.y) continue; // both resolve to the same point
    laidEdges.push({ fedge, x1: a.x, y1: a.y, x2: b.x, y2: b.y });
  }

  return { width: WIDTH, height, groups: laidGroups, nodes: laidNodes, edges: laidEdges };
}
