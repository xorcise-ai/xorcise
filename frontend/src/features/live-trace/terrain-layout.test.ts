import { describe, expect, test } from "vitest";
import type { FoldedEdge, FoldedGroup, FoldedNode, FoldedTerrain } from "./terrain-fold";
import { layoutTerrainV2 } from "./terrain-layout";

/** Build a FoldedGroup with sane defaults; override what a test cares about. */
function group(partial: Partial<FoldedGroup["group"]> & { id: string }, discovered = true): FoldedGroup {
  return {
    group: {
      label: partial.id,
      description: null,
      kind: "segment",
      order: 0,
      hidden: false,
      discovered: false,
      discovery_condition: null,
      ...partial,
    },
    discovered,
  };
}

/** Build a FoldedNode with sane defaults; override what a test cares about. */
function node(partial: Partial<FoldedNode["node"]> & { id: string; group: string }, state: FoldedNode["state"] = "defined"): FoldedNode {
  return {
    node: {
      label: partial.id,
      type: "service",
      objective: false,
      description: null,
      discovery_condition: null,
      completion_condition: null,
      state: "defined",
      ...partial,
    },
    state,
  };
}

/** Build a FoldedEdge with sane defaults; override what a test cares about. */
function edge(partial: Partial<FoldedEdge["edge"]> & { id: string; src: string; dst: string }, active = true): FoldedEdge {
  return {
    edge: { label: null, active: false, ...partial },
    active,
  };
}

function terrain(partial: Partial<FoldedTerrain>): FoldedTerrain {
  return {
    groups: [],
    nodes: [],
    edges: [],
    pulse: { nodes: new Set(), groups: new Set(), edges: new Set() },
    ...partial,
  };
}

describe("layoutTerrainV2", () => {
  test("groups render as bands ordered by `order` ascending (low = top)", () => {
    const l = layoutTerrainV2(
      terrain({
        groups: [
          group({ id: "seg", order: 1 }),
          group({ id: "xorcise", order: 0 }),
        ],
      }),
    );
    const byId = new Map(l.groups.map((g) => [g.fgroup.group.id, g]));
    expect(byId.get("xorcise")!.y).toBeLessThan(byId.get("seg")!.y);
  });

  test("a node places within its own group's band, not another group's", () => {
    const l = layoutTerrainV2(
      terrain({
        groups: [group({ id: "a", order: 0 }), group({ id: "b", order: 1 })],
        nodes: [node({ id: "n1", group: "a" }), node({ id: "n2", group: "b" })],
      }),
    );
    const bandA = l.groups.find((g) => g.fgroup.group.id === "a")!;
    const bandB = l.groups.find((g) => g.fgroup.group.id === "b")!;
    const n1 = l.nodes.find((n) => n.fnode.node.id === "n1")!;
    const n2 = l.nodes.find((n) => n.fnode.node.id === "n2")!;

    expect(n1.y).toBeGreaterThanOrEqual(bandA.y);
    expect(n1.y).toBeLessThanOrEqual(bandA.y + bandA.h);
    expect(n2.y).toBeGreaterThanOrEqual(bandB.y);
    expect(n2.y).toBeLessThanOrEqual(bandB.y + bandB.h);

    // n1 is NOT inside band b's vertical range (bands don't overlap)
    expect(n1.y < bandB.y || n1.y > bandB.y + bandB.h).toBe(true);
  });

  test("each node carries its slot width (bandW / node-count) so the renderer can clamp its label", () => {
    const l = layoutTerrainV2(
      terrain({
        groups: [group({ id: "a", order: 0 })],
        nodes: [
          node({ id: "n1", group: "a" }),
          node({ id: "n2", group: "a" }),
          node({ id: "n3", group: "a" }),
        ],
      }),
    );
    // WIDTH 760 − 2·PAD(20) = 720 band width, split across 3 members → 240 each.
    for (const n of l.nodes) expect(n.slotW).toBe(240);
  });

  test("an edge between two placed nodes gets endpoints at their centres", () => {
    const l = layoutTerrainV2(
      terrain({
        groups: [group({ id: "a", order: 0 }), group({ id: "b", order: 1 })],
        nodes: [node({ id: "n1", group: "a" }), node({ id: "n2", group: "b" })],
        edges: [edge({ id: "e1", src: "n1", dst: "n2" })],
      }),
    );
    const n1 = l.nodes.find((n) => n.fnode.node.id === "n1")!;
    const n2 = l.nodes.find((n) => n.fnode.node.id === "n2")!;
    expect(l.edges).toHaveLength(1);
    const e = l.edges[0];
    expect(e.x1).toBe(n1.x);
    expect(e.y1).toBe(n1.y);
    expect(e.x2).toBe(n2.x);
    expect(e.y2).toBe(n2.y);
  });

  test("a hidden, undiscovered group is laid out (greyed handled by renderer), not omitted", () => {
    const l = layoutTerrainV2(
      terrain({
        groups: [
          group({ id: "visible", order: 0 }),
          group({ id: "hidden", order: 1, hidden: true }, false),
        ],
        nodes: [
          node({ id: "n1", group: "visible" }),
          node({ id: "n2", group: "hidden" }),
        ],
        edges: [edge({ id: "e1", src: "n1", dst: "n2" })],
      }),
    );
    expect(l.groups.map((g) => g.fgroup.group.id)).toEqual(["visible", "hidden"]);
    expect(l.nodes.map((n) => n.fnode.node.id)).toEqual(["n1", "n2"]);
    expect(l.edges).toHaveLength(1);
  });

  test("the SAME hidden group, once discovered, IS laid out with its nodes placed", () => {
    const l = layoutTerrainV2(
      terrain({
        groups: [
          group({ id: "visible", order: 0 }),
          group({ id: "hidden", order: 1, hidden: true }, true),
        ],
        nodes: [
          node({ id: "n1", group: "visible" }),
          node({ id: "n2", group: "hidden" }),
        ],
        edges: [edge({ id: "e1", src: "n1", dst: "n2" })],
      }),
    );
    expect(l.groups.map((g) => g.fgroup.group.id)).toEqual(["visible", "hidden"]);
    expect(l.nodes.map((n) => n.fnode.node.id)).toEqual(["n1", "n2"]);
    expect(l.edges).toHaveLength(1);
  });

  test("an edge whose dst is a GROUP id anchors to that group's band top-centre", () => {
    const l = layoutTerrainV2(
      terrain({
        groups: [group({ id: "a", order: 0 }), group({ id: "b", order: 1 })],
        nodes: [node({ id: "n1", group: "a" })],
        edges: [edge({ id: "e1", src: "n1", dst: "b" })],
      }),
    );
    const bandB = l.groups.find((g) => g.fgroup.group.id === "b")!;
    expect(l.edges).toHaveLength(1);
    const e = l.edges[0];
    expect(e.x2).toBe(bandB.x + bandB.w / 2);
    expect(e.y2).toBe(bandB.y);
  });

  test("endpoint nodes are collapsed onto their parent service's `routes`, not laid out as their own circles", () => {
    const l = layoutTerrainV2(
      terrain({
        groups: [group({ id: "infra", order: 0 })],
        nodes: [
          node({ id: "hs", group: "infra", type: "service" }),
          node({ id: "hs:register", group: "infra", type: "endpoint", label: "register" }),
          node({ id: "hs:join", group: "infra", type: "endpoint", label: "join" }),
          node({ id: "rc", group: "infra", type: "service" }),
        ],
      }),
    );
    const ids = l.nodes.map((n) => n.fnode.node.id);
    expect(ids).toContain("hs");
    expect(ids).toContain("rc");
    expect(ids).not.toContain("hs:register");
    expect(ids).not.toContain("hs:join");

    const hs = l.nodes.find((n) => n.fnode.node.id === "hs")!;
    expect(hs.routes).toEqual([
      { id: "hs:register", label: "register" },
      { id: "hs:join", label: "join" },
    ]);

    // a service with no endpoints has empty/absent routes
    const rc = l.nodes.find((n) => n.fnode.node.id === "rc")!;
    expect(rc.routes ?? []).toEqual([]);
  });

  test("a collapsed endpoint's state surfaces on its parent service node", () => {
    // hs:join firing (discovered) must light up the VISIBLE `hs` service node, even though the
    // endpoint itself is collapsed into the hover tooltip and never drawn.
    const l = layoutTerrainV2(
      terrain({
        groups: [group({ id: "infra", order: 0 })],
        nodes: [
          node({ id: "hs", group: "infra", type: "service" }, "defined"),
          node({ id: "hs:join", group: "infra", type: "endpoint", label: "join" }, "discovered"),
          node({ id: "rc", group: "infra", type: "service" }, "defined"),
        ],
      }),
    );
    const hs = l.nodes.find((n) => n.fnode.node.id === "hs")!;
    expect(hs.fnode.state).toBe("discovered"); // inherited from the collapsed hs:join endpoint
    const rc = l.nodes.find((n) => n.fnode.node.id === "rc")!;
    expect(rc.fnode.state).toBe("defined"); // no collapsed endpoints → unchanged
  });

  test("an edge to a collapsed endpoint re-anchors to the parent service", () => {
    const l = layoutTerrainV2(
      terrain({
        groups: [group({ id: "infra", order: 0 })],
        nodes: [
          node({ id: "agent", group: "infra", type: "agent" }),
          node({ id: "hs", group: "infra", type: "service" }),
          node({ id: "hs:register", group: "infra", type: "endpoint", label: "register" }),
        ],
        edges: [edge({ id: "e1", src: "agent", dst: "hs:register" })],
      }),
    );
    const agent = l.nodes.find((n) => n.fnode.node.id === "agent")!;
    const hs = l.nodes.find((n) => n.fnode.node.id === "hs")!;
    expect(l.edges).toHaveLength(1);
    const e = l.edges[0];
    expect(e.x1).toBe(agent.x);
    expect(e.y1).toBe(agent.y);
    expect(e.x2).toBe(hs.x);
    expect(e.y2).toBe(hs.y);
  });

  test("an edge to a collapsed endpoint drops when its parent service is not drawn", () => {
    const l = layoutTerrainV2(
      terrain({
        groups: [group({ id: "infra", order: 0 })],
        nodes: [
          node({ id: "agent", group: "infra", type: "agent" }),
          node({ id: "ghost:route", group: "infra", type: "endpoint", label: "route" }),
        ],
        edges: [edge({ id: "e1", src: "agent", dst: "ghost:route" })],
      }),
    );
    expect(l.edges).toEqual([]);
  });

  test("determinism: calling layoutTerrainV2 twice on the same input yields identical output", () => {
    const t = terrain({
      groups: [group({ id: "a", order: 0 }), group({ id: "b", order: 1 })],
      nodes: [node({ id: "n1", group: "a" }), node({ id: "n2", group: "b" })],
      edges: [edge({ id: "e1", src: "n1", dst: "n2" })],
    });
    const l1 = layoutTerrainV2(t);
    const l2 = layoutTerrainV2(t);
    expect(l1).toEqual(l2);
  });
});
