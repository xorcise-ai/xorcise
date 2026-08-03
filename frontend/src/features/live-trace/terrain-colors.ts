import type { FoldedGroup, FoldedNode } from "./terrain-fold";

/**
 * Pure terrain-map v2 color scheme (see terrain-map.tsx for the rendered application).
 *
 * Node fill:  grey = unknown · yellow = the agent node (only it) · blue = discovered ·
 *             green = enumerated · red = the unsolved objective (→ green once enumerated).
 * Group box:  XORCISE-infra = blue · agent workspace = yellow (the only yellow group) · mission
 *             segment = grey (unknown) → blue (discovered) → green (all its nodes enumerated).
 * Edge:       the currently-probing (pulsed) edge = yellow · other active edges = blue.
 *
 * Hard OTel gate: the whole mission plane (segment groups + their nodes) reads as unknown (grey)
 * until the agent has emitted OTel — signalled by the fixed infra collector node being discovered.
 * The XORCISE-infra and agent-workspace planes are never gated.
 */

export const T = {
  grey: "var(--color-muted-foreground)",
  yellow: "var(--color-terrain-hot)",
  blue: "var(--color-terrain-cool)",
  green: "var(--color-ok)",
  red: "var(--color-err)",
} as const;

/** The OTel collector's fixed infra-scaffold node id; its discovery is the "agent is emitting
 *  OTel" signal that ungates the mission plane. */
export const OTEL_COLLECTOR_NODE_ID = "collector";

/** True once the agent is emitting OTel — i.e. the collector node has been discovered. */
export function otelActive(nodes: readonly FoldedNode[]): boolean {
  return nodes.some((n) => n.node.id === OTEL_COLLECTOR_NODE_ID && n.state !== "defined");
}

/** Fill color for a node, given its group's `kind` and whether the agent is emitting OTel. */
export function nodeColor(fn: FoldedNode, groupKind: string | undefined, otel: boolean): string {
  if (fn.node.type === "agent") return T.yellow; // the agent node is the only yellow node
  // Hard OTel gate: a mission-plane (segment) node stays unknown until the agent emits OTel.
  if (groupKind === "segment" && !otel) return T.grey;
  if (fn.node.objective) return fn.state === "completed" ? T.green : T.red;
  switch (fn.state) {
    case "defined":
      return T.grey;
    case "discovered":
      return T.blue;
    case "completed":
      return T.green;
  }
}

export interface GroupStyle {
  stroke: string;
  /** true => solid box (discovered/always-shown); false => dashed, greyed (undiscovered/gated). */
  solid: boolean;
}

/** Box stroke + solid/dashed for a group. `allNodesEnumerated` is whether every member node of a
 *  mission segment has been enumerated (completed). XORCISE-infra is always blue and the agent
 *  workspace always yellow (the only yellow group); a mission segment stays grey (unknown) until
 *  the agent emits OTel AND it's discovered, then goes BLUE (discovered) and finally GREEN once all
 *  its nodes are enumerated. */
export function groupStyle(
  fg: FoldedGroup,
  otel: boolean,
  allNodesEnumerated: boolean,
): GroupStyle {
  switch (fg.group.kind) {
    case "infra":
      return { stroke: T.blue, solid: true };
    case "agent":
      return { stroke: T.yellow, solid: true };
    default: {
      // mission segment, OTel-gated: unknown → discovered (blue) → all nodes enumerated (green).
      if (!otel || !fg.discovered) return { stroke: T.grey, solid: false };
      return allNodesEnumerated ? { stroke: T.green, solid: true } : { stroke: T.blue, solid: true };
    }
  }
}

/** Stroke for an edge: the currently-probing (pulsed) edge is yellow, other active edges are blue,
 *  an inactive edge returns null (the caller draws it in the border color). */
export function edgeColor(active: boolean, pulsed: boolean): string | null {
  if (pulsed) return T.yellow;
  if (active) return T.blue;
  return null;
}
