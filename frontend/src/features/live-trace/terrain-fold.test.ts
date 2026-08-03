import { describe, expect, it } from "vitest";
import { foldTerrain, foldIndexForEvent, probingPathEdgeIds, pulseIdsForIndex, deriveInfraRows } from "./terrain-fold";
import type { ResolvedTerrainV2 } from "@/lib/api/types";

const base = (updates: ResolvedTerrainV2["updates"]): ResolvedTerrainV2 => ({
  run_id: "r1",
  mission_id: "c1",
  summary: null,
  groups: [{ id: "g", label: "g", description: null, kind: "segment", order: 0, hidden: false, discovered: false }],
  nodes: [
    {
      id: "web",
      label: "web",
      group: "g",
      type: "service",
      objective: false,
      description: null,
      discovery_condition: null,
      completion_condition: null,
      state: "defined",
    },
  ],
  edges: [{ id: "e", src: "web", dst: "web", label: null, active: false }],
  updates,
  attribution: null,
  objective_id: null,
});

function nodeState(f: ReturnType<typeof foldTerrain>, id: string) {
  return f.nodes.find((n) => n.node.id === id)!.state;
}

function groupDiscovered(f: ReturnType<typeof foldTerrain>, id: string) {
  return f.groups.find((g) => g.group.id === id)!.discovered;
}

function edgeActive(f: ReturnType<typeof foldTerrain>, id: string) {
  return f.edges.find((e) => e.edge.id === id)!.active;
}

describe("foldTerrain", () => {
  it("folds to latest: applies every update (node ends completed, group discovered, edge active)", () => {
    const t = base([
      { seq: 0, target_kind: "node", target_id: "web", event_id: "e1", state: "discovered", discovered: null, active: null },
      { seq: 1, target_kind: "node", target_id: "web", event_id: "e2", state: "completed", discovered: null, active: null },
      { seq: 2, target_kind: "group", target_id: "g", event_id: "e3", state: null, discovered: true, active: null },
      { seq: 3, target_kind: "edge", target_id: "e", event_id: "e4", state: null, discovered: null, active: true },
    ]);

    const f = foldTerrain(t, t.updates!.length - 1);

    expect(nodeState(f, "web")).toBe("completed");
    expect(groupDiscovered(f, "g")).toBe(true);
    expect(edgeActive(f, "e")).toBe(true);
  });

  it("rewinds: folding to an earlier index leaves the node at the earlier state", () => {
    const t = base([
      { seq: 0, target_kind: "node", target_id: "web", event_id: "e1", state: "discovered", discovered: null, active: null },
      { seq: 1, target_kind: "node", target_id: "web", event_id: "e2", state: "completed", discovered: null, active: null },
    ]);

    const f = foldTerrain(t, 0);

    expect(nodeState(f, "web")).toBe("discovered");
  });

  it("is monotonic advance-only: a discovered update after completed does not downgrade", () => {
    const t = base([
      { seq: 0, target_kind: "node", target_id: "web", event_id: "e1", state: "completed", discovered: null, active: null },
      { seq: 1, target_kind: "node", target_id: "web", event_id: "e2", state: "discovered", discovered: null, active: null },
    ]);

    const f = foldTerrain(t, t.updates!.length - 1);

    expect(nodeState(f, "web")).toBe("completed");
  });

  it("base fold (k=-1): nodes/groups/edges sit at their DTO base values, pulse is empty", () => {
    const t = base([
      { seq: 0, target_kind: "node", target_id: "web", event_id: "e1", state: "discovered", discovered: null, active: null },
    ]);

    const f = foldTerrain(t, -1);

    expect(nodeState(f, "web")).toBe("defined");
    expect(groupDiscovered(f, "g")).toBe(false);
    expect(edgeActive(f, "e")).toBe(false);
    expect(f.pulse.nodes.size).toBe(0);
    expect(f.pulse.groups.size).toBe(0);
    expect(f.pulse.edges.size).toBe(0);
  });

  it("ignores updates targeting unknown ids", () => {
    const t = base([
      { seq: 0, target_kind: "node", target_id: "ghost", event_id: "e1", state: "completed", discovered: null, active: null },
    ]);

    const f = foldTerrain(t, 0);

    expect(nodeState(f, "web")).toBe("defined");
  });

  it("carries a supplied pulseIds set through, partitioned by target_kind", () => {
    const t = base([
      { seq: 0, target_kind: "node", target_id: "web", event_id: "e2", state: "discovered", discovered: null, active: null },
      { seq: 1, target_kind: "edge", target_id: "e", event_id: "e2", state: null, discovered: null, active: true },
    ]);

    const f = foldTerrain(t, 1, pulseIdsForIndex(t.updates!, 1));

    expect([...f.pulse.nodes]).toEqual(["web"]);
    expect([...f.pulse.edges]).toEqual(["e"]);
    expect(f.pulse.groups.size).toBe(0);
  });

  it("defaults pulse sets to empty when pulseIds is omitted or null", () => {
    const t = base([
      { seq: 0, target_kind: "node", target_id: "web", event_id: "e1", state: "discovered", discovered: null, active: null },
    ]);

    const omitted = foldTerrain(t, 0);
    const nulled = foldTerrain(t, 0, null);

    expect(omitted.pulse.nodes.size).toBe(0);
    expect(nulled.pulse.nodes.size).toBe(0);
  });
});

describe("foldIndexForEvent", () => {
  it("returns the LAST index for an event with multiple updates", () => {
    const updates: ResolvedTerrainV2["updates"] = [
      { seq: 0, target_kind: "node", target_id: "web", event_id: "e2", state: "discovered", discovered: null, active: null },
      { seq: 1, target_kind: "edge", target_id: "e", event_id: "e2", state: null, discovered: null, active: true },
      { seq: 2, target_kind: "node", target_id: "web", event_id: "e3", state: "completed", discovered: null, active: null },
    ];

    expect(foldIndexForEvent(updates, "e2")).toBe(1);
  });

  it("returns null for an event id absent from updates", () => {
    const updates: ResolvedTerrainV2["updates"] = [
      { seq: 0, target_kind: "node", target_id: "web", event_id: "e1", state: "discovered", discovered: null, active: null },
    ];

    expect(foldIndexForEvent(updates, "does-not-exist")).toBeNull();
  });
});

describe("pulseIdsForIndex", () => {
  it("returns BOTH ids touched by updates sharing the boundary event_id", () => {
    const updates: ResolvedTerrainV2["updates"] = [
      { seq: 0, target_kind: "node", target_id: "web", event_id: "e2", state: "discovered", discovered: null, active: null },
      { seq: 1, target_kind: "edge", target_id: "e", event_id: "e2", state: null, discovered: null, active: true },
    ];

    expect(pulseIdsForIndex(updates, 1)).toEqual(new Set(["web", "e"]));
  });

  it("returns just the single target id for an infra update (event_id null)", () => {
    const updates: ResolvedTerrainV2["updates"] = [
      { seq: 0, target_kind: "node", target_id: "web", event_id: null, state: "discovered", discovered: null, active: null },
    ];

    expect(pulseIdsForIndex(updates, 0)).toEqual(new Set(["web"]));
  });

  it("returns an empty set for an out-of-range index", () => {
    const updates: ResolvedTerrainV2["updates"] = [
      { seq: 0, target_kind: "node", target_id: "web", event_id: "e1", state: "discovered", discovered: null, active: null },
    ];

    expect(pulseIdsForIndex(updates, 5)).toEqual(new Set());
    expect(pulseIdsForIndex(updates, -1)).toEqual(new Set());
  });

  it("pulses the WHOLE infra activity — all updates sharing the boundary ts", () => {
    const updates: ResolvedTerrainV2["updates"] = [
      { seq: 0, target_kind: "node", target_id: "hs:join", event_id: null, ts: "2026-01-01T00:00:10Z", state: "discovered", discovered: null, active: null },
      { seq: 1, target_kind: "edge", target_id: "m:agent-hs", event_id: null, ts: "2026-01-01T00:00:10Z", state: null, discovered: null, active: true },
      { seq: 2, target_kind: "node", target_id: "rc:prompt", event_id: null, ts: "2026-01-01T00:00:20Z", state: "discovered", discovered: null, active: null },
    ];
    // selecting the join edge lights both the Headscale node and its edge (one instant), not rc:prompt
    expect(pulseIdsForIndex(updates, 1)).toEqual(new Set(["hs:join", "m:agent-hs"]));
  });
});

describe("foldIndexForEvent (infra ids)", () => {
  it("resolves an infra:<seq> id straight to that fold index", () => {
    const updates: ResolvedTerrainV2["updates"] = [
      { seq: 0, target_kind: "node", target_id: "hs:join", event_id: null, ts: "t", state: "discovered", discovered: null, active: null },
      { seq: 1, target_kind: "edge", target_id: "m:agent-hs", event_id: null, ts: "t", state: null, discovered: null, active: true },
    ];
    expect(foldIndexForEvent(updates, "infra:1")).toBe(1);
    expect(foldIndexForEvent(updates, "infra:9")).toBeNull(); // out of range
  });
});

describe("deriveInfraRows", () => {
  it("collapses infra updates sharing a ts into one row targeting the activity's last seq", () => {
    const updates: ResolvedTerrainV2["updates"] = [
      { seq: 0, target_kind: "node", target_id: "hs:join", event_id: null, ts: "2026-01-01T00:00:10Z", state: "discovered", discovered: null, active: null, note: "Agent joined the tailnet" },
      { seq: 1, target_kind: "edge", target_id: "m:agent-hs", event_id: null, ts: "2026-01-01T00:00:10Z", state: null, discovered: null, active: true, note: "Agent joined the tailnet" },
      { seq: 2, target_kind: "node", target_id: "rc:prompt", event_id: null, ts: "2026-01-01T00:00:20Z", state: "discovered", discovered: null, active: null, note: "Fetched the run brief from run-control" },
    ];
    const rows = deriveInfraRows(updates);
    expect(rows.map((r) => r.id)).toEqual(["infra:1", "infra:2"]); // join collapsed (max seq 1), then brief
    expect(rows[0].label).toBe("Agent joined the tailnet");
    expect(rows[0].targetId).toBe("hs:join"); // node preferred over the m:agent-hs edge for the icon
    expect(rows[1].label).toBe("Fetched the run brief from run-control");
  });

  it("excludes a non-infra event_id=null update (the objective-grade update)", () => {
    const updates: ResolvedTerrainV2["updates"] = [
      { seq: 0, target_kind: "node", target_id: "web", event_id: null, ts: "2026-01-01T00:00:30Z", state: "completed", discovered: null, active: null, note: "objective solved" },
    ];
    expect(deriveInfraRows(updates)).toEqual([]);
  });

  it("ignores mission-plane (event_id set) updates", () => {
    const updates: ResolvedTerrainV2["updates"] = [
      { seq: 0, target_kind: "node", target_id: "web", event_id: "ev1", ts: "2026-01-01T00:00:05Z", state: "discovered", discovered: null, active: null, note: "probed the login form" },
    ];
    expect(deriveInfraRows(updates)).toEqual([]);
  });
});

describe("foldIndexForEvent — sub-event tolerance (time-travel)", () => {
  const upd = (event_id: string): NonNullable<ResolvedTerrainV2["updates"]>[number] => ({
    seq: 0, target_kind: "node", target_id: "web", event_id, state: "discovered", discovered: null, active: null,
  });

  it("a :tool click finds its span's update even when the update is keyed on :out", () => {
    // The trace selects the terminal COMMAND (:tool), but the attributor keyed the update on the
    // OUTPUT (:out). Matching on the span base must still locate it — otherwise k falls back to the
    // latest fold and the map shows the fully-completed (green) state (the reported bug).
    const updates = [upd("X=:out"), upd("Y=:out")];
    expect(foldIndexForEvent(updates, "X=:tool")).toBe(0);
    expect(foldIndexForEvent(updates, "Y=:tool")).toBe(1);
  });

  it("still returns the LAST index for the span, and null when the span produced no update", () => {
    const updates = [upd("X=:out"), upd("X=:tool"), upd("Z=:out")];
    expect(foldIndexForEvent(updates, "X=:anything")).toBe(1); // last update on span X
    expect(foldIndexForEvent(updates, "Q=:tool")).toBeNull(); // span Q never acted
  });
});

describe("probingPathEdgeIds", () => {
  // agent --e-agent-web--> web --e-web-internal--> internal
  const edges = [
    { id: "e-agent-web", src: "agent", dst: "web" },
    { id: "e-web-internal", src: "web", dst: "internal" },
  ];
  const agents = new Set(["agent"]);

  it("lights the FULL path from a probed node all the way back to the agent", () => {
    expect(probingPathEdgeIds(new Set(["internal"]), edges, agents)).toEqual(
      new Set(["e-web-internal", "e-agent-web"]),
    );
  });

  it("from an intermediate node lights only the edges up to the agent", () => {
    expect(probingPathEdgeIds(new Set(["web"]), edges, agents)).toEqual(new Set(["e-agent-web"]));
  });

  it("stops at the agent and terminates on cycles", () => {
    const cyc = [
      { id: "a", src: "x", dst: "y" },
      { id: "b", src: "y", dst: "x" },
    ];
    expect(probingPathEdgeIds(new Set(["y"]), cyc, new Set())).toEqual(new Set(["a", "b"]));
  });

  it("returns empty for an empty seed", () => {
    expect(probingPathEdgeIds(new Set(), edges, agents)).toEqual(new Set());
  });
});

describe("foldTerrain — agent entry-edge backstop", () => {
  // agent --e-agent-web--> web --e-web-internal--> internal. The attributor records the agent
  // REACHING web as node discovery but routinely omits the agent->web arrival edge; the backstop
  // activates it deterministically when web is reached (its only inbound edge is from the agent).
  const withAgent = (updates: ResolvedTerrainV2["updates"]): ResolvedTerrainV2 => ({
    run_id: "r1",
    mission_id: "c1",
    summary: null,
    groups: [
      { id: "dmz", label: "dmz", description: null, kind: "segment", order: 0, hidden: false, discovered: false },
    ],
    nodes: [
      { id: "agent", label: "Agent", group: "aw", type: "agent", objective: false, description: null, discovery_condition: null, completion_condition: null, state: "defined" },
      { id: "web", label: "web", group: "dmz", type: "service", objective: false, description: null, discovery_condition: null, completion_condition: null, state: "defined" },
      { id: "internal", label: "internal", group: "internal_net", type: "service", objective: true, description: null, discovery_condition: null, completion_condition: null, state: "defined" },
    ],
    edges: [
      { id: "e-agent-web", src: "agent", dst: "web", label: null, active: false },
      { id: "e-web-internal", src: "web", dst: "internal", label: null, active: false },
    ],
    updates,
    attribution: null,
    objective_id: "internal",
  });

  const edgeActive = (f: ReturnType<typeof foldTerrain>, id: string) =>
    f.edges.find((e) => e.edge.id === id)!.active;

  it("activates the agent->web entry edge when web is discovered, even with no edge update", () => {
    const t = withAgent([
      { seq: 0, target_kind: "node", target_id: "web", event_id: "e1", state: "discovered", discovered: null, active: null },
    ]);
    const f = foldTerrain(t, t.updates!.length - 1);
    expect(edgeActive(f, "e-agent-web")).toBe(true);
    // web->internal is NOT agent-sourced, so the backstop leaves it inactive until the model acts.
    expect(edgeActive(f, "e-web-internal")).toBe(false);
  });

  it("does NOT activate the entry edge before web is reached (respects time-travel)", () => {
    const t = withAgent([
      { seq: 0, target_kind: "node", target_id: "web", event_id: "e1", state: "discovered", discovered: null, active: null },
    ]);
    const f = foldTerrain(t, -1); // base: nothing folded, web still defined
    expect(edgeActive(f, "e-agent-web")).toBe(false);
  });

  it("does NOT auto-activate an agent edge when the node also has a non-agent inbound edge", () => {
    // internal is reachable both directly from the agent AND via the web pivot: the agent's direct
    // arrival can't be assumed, so the backstop must not light agent->internal on discovery alone.
    const t = withAgent([
      { seq: 0, target_kind: "node", target_id: "internal", event_id: "e1", state: "discovered", discovered: null, active: null },
    ]);
    t.edges = [...(t.edges ?? []), { id: "e-agent-internal", src: "agent", dst: "internal", label: null, active: false }];
    const f = foldTerrain(t, t.updates!.length - 1);
    expect(edgeActive(f, "e-agent-internal")).toBe(false);
  });
});
