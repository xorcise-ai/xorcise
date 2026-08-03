import { describe, expect, it } from "vitest";
import type { FoldedGroup, FoldedNode, NodeState } from "./terrain-fold";
import { T, edgeColor, groupStyle, nodeColor, otelActive, OTEL_COLLECTOR_NODE_ID } from "./terrain-colors";

const node = (
  over: Partial<FoldedNode["node"]> & { state?: NodeState } = {},
): FoldedNode => {
  const { state = "defined", ...n } = over;
  return {
    state,
    node: {
      id: "n",
      label: "n",
      group: "g",
      type: "service",
      objective: false,
      description: null,
      discovery_condition: null,
      completion_condition: null,
      state: "defined",
      ...n,
    } as FoldedNode["node"],
  };
};

const group = (kind: string, discovered: boolean): FoldedGroup => ({
  discovered,
  group: { id: "g", label: "g", description: null, kind, order: 0, hidden: false, discovered } as FoldedGroup["group"],
});

describe("nodeColor", () => {
  it("the agent node is always yellow, regardless of state/otel/objective", () => {
    expect(nodeColor(node({ type: "agent", state: "defined" }), "agent", false)).toBe(T.yellow);
    expect(nodeColor(node({ type: "agent", state: "completed" }), "agent", true)).toBe(T.yellow);
  });

  it("hard OTel gate: a mission (segment) node is grey until the agent emits OTel", () => {
    expect(nodeColor(node({ state: "discovered" }), "segment", false)).toBe(T.grey);
    expect(nodeColor(node({ state: "completed" }), "segment", false)).toBe(T.grey);
    expect(nodeColor(node({ objective: true, state: "discovered" }), "segment", false)).toBe(T.grey);
  });

  it("mission node colors by state once OTel is active", () => {
    expect(nodeColor(node({ state: "defined" }), "segment", true)).toBe(T.grey);
    expect(nodeColor(node({ state: "discovered" }), "segment", true)).toBe(T.blue);
    expect(nodeColor(node({ state: "completed" }), "segment", true)).toBe(T.green);
  });

  it("the objective is red until enumerated, then green (never blue), once OTel is active", () => {
    expect(nodeColor(node({ objective: true, state: "defined" }), "segment", true)).toBe(T.red);
    expect(nodeColor(node({ objective: true, state: "discovered" }), "segment", true)).toBe(T.red);
    expect(nodeColor(node({ objective: true, state: "completed" }), "segment", true)).toBe(T.green);
  });

  it("infra-plane nodes are NOT OTel-gated (they color by state even before OTel)", () => {
    expect(nodeColor(node({ state: "discovered" }), "infra", false)).toBe(T.blue);
    expect(nodeColor(node({ state: "completed" }), "infra", false)).toBe(T.green);
    expect(nodeColor(node({ state: "defined" }), "infra", false)).toBe(T.grey);
  });
});

describe("otelActive", () => {
  it("is true iff the collector node is discovered", () => {
    expect(otelActive([node({ id: OTEL_COLLECTOR_NODE_ID, state: "discovered" })])).toBe(true);
    expect(otelActive([node({ id: OTEL_COLLECTOR_NODE_ID, state: "defined" })])).toBe(false);
    expect(otelActive([node({ id: "web", state: "completed" })])).toBe(false);
  });
});

describe("groupStyle", () => {
  it("the XORCISE-infra group is always solid blue", () => {
    expect(groupStyle(group("infra", false), false, false)).toEqual({ stroke: T.blue, solid: true });
  });
  it("the agent-workspace group is always solid yellow (the ONLY yellow group)", () => {
    expect(groupStyle(group("agent", false), false, true)).toEqual({ stroke: T.yellow, solid: true });
  });
  it("a mission segment is grey until the agent emits OTel AND the segment is discovered", () => {
    expect(groupStyle(group("segment", true), false, false)).toEqual({ stroke: T.grey, solid: false }); // gated on OTel
    expect(groupStyle(group("segment", false), true, false)).toEqual({ stroke: T.grey, solid: false }); // not discovered
  });
  it("a discovered segment is BLUE until all its nodes are enumerated, then GREEN", () => {
    // discovered, OTel on, but not everything enumerated yet -> blue
    expect(groupStyle(group("segment", true), true, false)).toEqual({ stroke: T.blue, solid: true });
    // every node enumerated -> green
    expect(groupStyle(group("segment", true), true, true)).toEqual({ stroke: T.green, solid: true });
  });
});

describe("edgeColor", () => {
  it("the currently-probing (pulsed) edge is yellow; other active edges are blue; inactive is null", () => {
    expect(edgeColor(true, true)).toBe(T.yellow); // active + pulsed -> probing
    expect(edgeColor(false, true)).toBe(T.yellow); // pulsed wins even if not yet marked active
    expect(edgeColor(true, false)).toBe(T.blue); // active, not probing
    expect(edgeColor(false, false)).toBeNull(); // inactive
  });
});
