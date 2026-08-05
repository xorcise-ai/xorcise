import { http, HttpResponse } from "msw";
import { describe, expect, test, vi } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/msw/server";
import type { ResolvedTerrainV2 } from "@/lib/api/types";
import { TerrainMap } from "./terrain-map";

function terrain(partial: Partial<ResolvedTerrainV2>): ResolvedTerrainV2 {
  return {
    run_id: "r1",
    mission_id: "c1",
    summary: null,
    groups: [],
    nodes: [],
    edges: [],
    updates: [],
    attribution: null,
    objective_id: null,
    ...partial,
  } as ResolvedTerrainV2;
}

function mount(body: ResolvedTerrainV2) {
  server.use(http.get("*/runs/r1/terrain2", () => HttpResponse.json(body)));
  return renderWithProviders(<TerrainMap runId="r1" />);
}

// The mission plane stays grey until the agent emits OTel; a discovered `collector` node is that
// signal. Mission-node color tests spread these in so the segment nodes color from their state.
const OTEL_GROUP = { id: "xinfra", label: "XORCISE", description: null, kind: "infra", order: -1, hidden: false, discovered: true } as NonNullable<ResolvedTerrainV2["groups"]>[number];
const OTEL_NODE = {
  id: "collector", label: "OTel", group: "xinfra", type: "control_plane", objective: false,
  description: null, discovery_condition: null, completion_condition: null, state: "discovered",
} as NonNullable<ResolvedTerrainV2["nodes"]>[number];

describe("TerrainMap", () => {
  test("renders a node per DTO node", async () => {
    const { container } = mount(
      terrain({
        groups: [{ id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
        nodes: [
          { id: "web", label: "web", group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "db", label: "db", group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    expect(await screen.findByText("web")).toBeInTheDocument();
    expect(await screen.findByText("db")).toBeInTheDocument();
    const svg = container.querySelector('[data-testid="terrain-svg"]');
    expect(svg).toBeInTheDocument();
    expect(container.querySelectorAll('[data-node-id]').length).toBe(2);
  });

  // Minimal terrain content so the map renders (an empty terrain shows the "No terrain yet" state).
  const oneNode = {
    groups: [
      { id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
    ],
    nodes: [
      { id: "web", label: "web", group: "g", type: "service", objective: false, description: null,
        discovery_condition: null, completion_condition: null, state: "defined" },
    ],
  } as Partial<ResolvedTerrainV2>;

  test("shows exactly two summary lines and expands the same layer over the map", async () => {
    const summary =
      "A purely offline Windows DFIR reconstruction: parse the corrupted Microsoft-Windows-RPC event log and assemble the flag from five recovered facts.";
    mount(terrain({ ...oneNode, summary }));
    // One full accessible copy lives inside its own two-line clipping viewport.
    const p = await screen.findByTestId("terrain-summary");
    expect(p).toHaveTextContent(/assemble the flag from five recovered facts/);
    expect(p.className).toContain("overflow-hidden");
    const panel = screen.getByTestId("terrain-summary-overlay");
    expect(panel.className).toContain("absolute");
    expect(panel.querySelectorAll("p")).toHaveLength(1);
    expect(panel).not.toHaveAttribute("data-expanded");
    Object.defineProperty(panel, "scrollHeight", { value: 180, configurable: true });
    Object.defineProperty(panel.parentElement, "offsetHeight", { value: 52, configurable: true });
    // No dead control while nothing overflows (jsdom measures 0/0 → fits) …
    expect(screen.queryByRole("button", { name: /show more/i })).toBeNull();
    // … the inline toggle appears once the natural copy is taller than two lines.
    Object.defineProperty(p, "scrollHeight", { value: 66, configurable: true });
    Object.defineProperty(p, "clientHeight", { value: 66, configurable: true });
    act(() => {
      window.dispatchEvent(new Event("resize"));
    });
    const more = await screen.findByRole("button", { name: /show more/i });
    expect(p.style.maxHeight).toBe("35.2px");
    expect(more.className).toContain("absolute");
    expect(more.className).toContain("right-4");
    expect(more.className).toContain("text-primary");
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(more).toHaveAttribute("aria-controls", p.id);
    fireEvent.click(more);
    // The SAME panel and paragraph remain mounted and extend over the map; only the control label
    // and expansion state change, so there is no duplicate-sheet swap.
    expect(screen.getByTestId("terrain-summary-overlay")).toBe(panel);
    expect(screen.getByTestId("terrain-summary")).toBe(p);
    expect(panel).toHaveAttribute("data-expanded", "true");
    expect(p.style.maxHeight).toBe("66px");
    // Full copy (66px) plus the panel chrome (52px slot - 35.2px two-line copy).
    expect(panel.style.maxHeight).toBe("82.8px");
    expect(panel.style.overflowY).toBe("hidden");
    const less = screen.getByRole("button", { name: /show less/i });
    expect(less).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(less);
    expect(panel).not.toHaveAttribute("data-expanded");
    expect(p.style.maxHeight).toBe("35.2px");
    expect(screen.getByRole("button", { name: /show more/i })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  test("renders no summary block when the terrain has none", async () => {
    const { container } = mount(terrain({ ...oneNode, summary: null }));
    await screen.findByText("web");
    expect(container.querySelector('[data-testid="terrain-summary"]')).toBeNull();
  });

  test("a long node label is clamped to two lines and keeps the full text in a title (no sideways overlap)", async () => {
    const longLabel =
      "web page on :80 (Apache/version-based) — CVE-2021-41773 encoded-dot traversal → /flag";
    const { container } = mount(
      terrain({
        // three members in one band → each slot is narrow (240px), so this label must wrap/clamp
        groups: [{ id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
        nodes: [
          { id: "web", label: longLabel, group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "db", label: "db", group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "cache", label: "cache", group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("db");
    const web = container.querySelector('[data-node-id="web"]')!;
    // the label is rendered as at most two <text> lines, never one unbounded run
    const lines = web.querySelectorAll("[data-node-label] text");
    expect(lines.length).toBeGreaterThan(0);
    expect(lines.length).toBeLessThanOrEqual(2);
    // truncated → the last visible line is ellipsized, and the full label is recoverable via <title>
    expect(lines[lines.length - 1].textContent).toContain("…");
    expect(web.querySelector("[data-node-label] title")!.textContent).toBe(longLabel);
    // a short label stays a single line with no ellipsis and no title
    const db = container.querySelector('[data-node-id="db"]')!;
    const dbLines = db.querySelectorAll("[data-node-label] text");
    expect(dbLines.length).toBe(1);
    expect(dbLines[0].textContent).toBe("db");
    expect(db.querySelector("[data-node-label] title")).not.toBeInTheDocument();
  });

  test("only the last attributed-action node pulses — not every discovered node", async () => {
    // two discovered mission nodes; only `web` is the target of an attributed (event_id) update,
    // so ONLY `web` gets the in-progress halo. Previously EVERY discovered node pulsed.
    const { container } = mount(
      terrain({
        groups: [OTEL_GROUP, { id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
        nodes: [
          OTEL_NODE,
          { id: "web", label: "web", group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "discovered" },
          { id: "db", label: "db", group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "discovered" },
        ] as ResolvedTerrainV2["nodes"],
        updates: [
          { seq: 0, target_kind: "node", target_id: "web", event_id: "ev1", state: "discovered",
            discovered: null, active: null, note: null },
        ],
      }),
    );
    await screen.findByText("web");
    const web = container.querySelector('[data-node-id="web"]')!;
    const db = container.querySelector('[data-node-id="db"]')!;
    // the acted-on node pulses; the other discovered node does NOT
    expect(web.querySelector('[data-node-hot="true"]')).toBeInTheDocument();
    expect(db.querySelector(".tm-pulse-yellow")).not.toBeInTheDocument();
    // both are still blue (old-theme: dark disc, state color on the ring border + inner dot)
    const body = web.querySelector('circle[data-node-fill]')!;
    expect(body.getAttribute("fill")).toBe("var(--color-card)");
    expect(body.getAttribute("stroke")).toBe("var(--color-terrain-cool)");
    expect(web.querySelector('[data-node-dot="true"]')!.getAttribute("fill")).toBe("var(--color-terrain-cool)");
  });

  test("a completed (enumerated) node is green (no halo); completion is the color, NOT a black dot", async () => {
    const { container } = mount(
      terrain({
        groups: [OTEL_GROUP, { id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
        nodes: [
          OTEL_NODE,
          { id: "web", label: "web", group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "completed" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("web");
    const node = container.querySelector('[data-node-id="web"]')!;
    expect(node.getAttribute("data-node-state")).toBe("completed");
    expect(node.querySelector(".tm-pulse-yellow")).not.toBeInTheDocument();
    const body = node.querySelector('circle[data-node-fill]')!;
    expect(body.getAttribute("stroke")).toBe("var(--color-ok)");
    // green conveys completion; the old black done-dot is gone, the inner dot is just the state color
    expect(node.querySelector('[data-node-done="true"]')).not.toBeInTheDocument();
    expect(node.querySelector('[data-node-dot="true"]')!.getAttribute("fill")).toBe("var(--color-ok)");
  });

  test("a mission node stays grey (unknown) until the agent emits OTel, even if discovered", async () => {
    const { container } = mount(
      terrain({
        groups: [{ id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
        nodes: [
          { id: "web", label: "web", group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "discovered" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("web");
    const node = container.querySelector('[data-node-id="web"]')!;
    // no collector node => OTel not active => the mission plane reads as unknown (grey ring)
    expect(node.querySelector('circle[data-node-fill]')!.getAttribute("stroke")).toBe("var(--color-muted-foreground)");
  });

  test("the objective is the ONLY node with the X glyph — red ring while open, no inner dot", async () => {
    const { container } = mount(
      terrain({
        groups: [OTEL_GROUP, { id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
        nodes: [
          OTEL_NODE,
          { id: "flag", label: "flag", group: "g", type: "service", objective: true, description: null,
            discovery_condition: null, completion_condition: null, state: "discovered" },
        ] as ResolvedTerrainV2["nodes"],
        objective_id: "flag",
      }),
    );
    await screen.findByText("flag");
    const node = container.querySelector('[data-node-id="flag"]')!;
    expect(node.querySelector('circle[data-node-fill]')!.getAttribute("stroke")).toBe("var(--color-err)");
    expect(node.querySelector('[data-objective-glyph="x"]')).toBeInTheDocument();
    // the objective carries the X instead of the inner dot
    expect(node.querySelector('[data-node-dot="true"]')).not.toBeInTheDocument();
    // a NON-objective node has a dot, never the X
    const otel = container.querySelector('[data-node-id="collector"]')!;
    expect(otel.querySelector('[data-objective-glyph="x"]')).not.toBeInTheDocument();
    expect(otel.querySelector('[data-node-dot="true"]')).toBeInTheDocument();
  });

  test("an enumerated objective keeps the X glyph but turns green", async () => {
    const { container } = mount(
      terrain({
        groups: [OTEL_GROUP, { id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
        nodes: [
          OTEL_NODE,
          { id: "flag", label: "flag", group: "g", type: "service", objective: true, description: null,
            discovery_condition: null, completion_condition: null, state: "completed" },
        ] as ResolvedTerrainV2["nodes"],
        objective_id: "flag",
      }),
    );
    await screen.findByText("flag");
    const node = container.querySelector('[data-node-id="flag"]')!;
    expect(node.querySelector('circle[data-node-fill]')!.getAttribute("stroke")).toBe("var(--color-ok)");
    expect(node.querySelector('[data-objective-glyph="x"]')).toBeInTheDocument();
  });

  test("a mission segment stays dashed/grey until OTel; enumerated + OTel renders it solid green", async () => {
    const { container } = mount(
      terrain({
        // no collector => OTel off => the discovered segment is still gated grey/dashed
        groups: [{ id: "vis", label: "Segment", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
        nodes: [
          { id: "n1", label: "n1", group: "vis", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("n1");
    const box = container.querySelector('[data-group-id="vis"]')!;
    const rect = box.querySelector("rect")!;
    expect(rect.getAttribute("stroke-dasharray")).toBeTruthy(); // gated: dashed
    expect(rect.getAttribute("stroke")).toBe("var(--color-muted-foreground)");
  });

  test("a discovered segment (OTel on) is BLUE while a member node is not yet enumerated; infra stays blue", async () => {
    const { container } = mount(
      terrain({
        groups: [
          OTEL_GROUP,
          { id: "vis", label: "Segment", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
        ],
        nodes: [
          OTEL_NODE,
          { id: "n1", label: "n1", group: "vis", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "discovered" }, // not yet enumerated
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("n1");
    const seg = container.querySelector('[data-group-id="vis"]')!.querySelector("rect")!;
    expect(seg.getAttribute("stroke-dasharray")).toBeFalsy(); // discovered: solid
    expect(seg.getAttribute("stroke")).toBe("var(--color-terrain-cool)"); // BLUE, not green (not all enumerated)
    const infra = container.querySelector('[data-group-id="xinfra"]')!.querySelector("rect")!;
    expect(infra.getAttribute("stroke")).toBe("var(--color-terrain-cool)"); // infra always blue
  });

  test("a segment turns GREEN only once ALL its nodes are enumerated", async () => {
    const { container } = mount(
      terrain({
        groups: [
          OTEL_GROUP,
          { id: "vis", label: "Segment", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
        ],
        nodes: [
          OTEL_NODE,
          { id: "n1", label: "n1", group: "vis", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "completed" },
          { id: "n2", label: "n2", group: "vis", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "completed" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("n1");
    const seg = container.querySelector('[data-group-id="vis"]')!.querySelector("rect")!;
    expect(seg.getAttribute("stroke")).toBe("var(--color-ok)"); // all nodes enumerated -> green
  });

  test("a network group never renders yellow, even when it is the pulsed delta (only the agent is yellow)", async () => {
    const { container } = mount(
      terrain({
        groups: [
          OTEL_GROUP,
          { id: "vis", label: "Segment", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
        ],
        nodes: [
          OTEL_NODE,
          { id: "n1", label: "n1", group: "vis", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "discovered" },
        ] as ResolvedTerrainV2["nodes"],
        // the last update targets the segment group -> it is the pulsed delta at the latest fold
        updates: [
          { seq: 0, target_kind: "group", target_id: "vis", event_id: "ev", discovered: true, state: null, active: null },
        ],
      }),
    );
    await screen.findByText("n1");
    const seg = container.querySelector('[data-group-id="vis"]')!.querySelector("rect")!;
    expect(seg.getAttribute("stroke")).not.toBe("var(--color-primary)"); // never amber/yellow
    expect(seg.getAttribute("stroke")).toBe("var(--color-terrain-cool)"); // its state color (blue) wins
  });

  test("an inactive edge renders solid; an active edge renders dotted", async () => {
    const { container } = mount(
      terrain({
        groups: [
          { id: "a", label: "A", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
          { id: "b", label: "B", description: null, kind: "segment", order: 1, hidden: false, discovered: true },
        ],
        nodes: [
          { id: "n1", label: "n1", group: "a", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "n2", label: "n2", group: "b", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
        edges: [
          { id: "e1", src: "n1", dst: "n2", label: null, active: false },
        ],
      }),
    );
    await screen.findByText("n1");
    const e1 = container.querySelector('[data-edge-id="e1"]')!;
    expect(e1.getAttribute("data-edge-active")).toBe("false");
    expect(e1.getAttribute("stroke-dasharray")).toBeFalsy();
  });

  test("an active edge renders dotted", async () => {
    const { container } = mount(
      terrain({
        groups: [
          { id: "a", label: "A", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
          { id: "b", label: "B", description: null, kind: "segment", order: 1, hidden: false, discovered: true },
        ],
        nodes: [
          { id: "n1", label: "n1", group: "a", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "n2", label: "n2", group: "b", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
        edges: [
          { id: "e1", src: "n1", dst: "n2", label: null, active: true },
        ],
      }),
    );
    await screen.findByText("n1");
    const e1 = container.querySelector('[data-edge-id="e1"]')!;
    expect(e1.getAttribute("data-edge-active")).toBe("true");
    expect(e1.getAttribute("stroke-dasharray")).toBeTruthy();
  });

  test("time-travel: rewinding to an earlier span shows the node at its earlier state and pulses the delta", async () => {
    server.use(
      http.get("*/runs/r1/terrain2", () =>
        HttpResponse.json(
          terrain({
            groups: [{ id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
            nodes: [
              { id: "web", label: "web", group: "g", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
            ] as ResolvedTerrainV2["nodes"],
            updates: [
              { seq: 0, target_kind: "node", target_id: "web", event_id: "e1", state: "discovered", discovered: null, active: null },
              { seq: 1, target_kind: "node", target_id: "web", event_id: "e2", state: "completed", discovered: null, active: null },
            ],
          }),
        ),
      ),
    );
    const { container, rerender } = renderWithProviders(
      <TerrainMap runId="r1" selectedEventId={null} />,
    );
    await screen.findByText("web");
    // latest view: node is completed
    let node = container.querySelector('[data-node-id="web"]')!;
    expect(node.getAttribute("data-node-state")).toBe("completed");

    // rewind to e1 — the node was only "discovered" at that point, and the delta (this update)
    // should carry the pulse (amber ring)
    rerender(<TerrainMap runId="r1" selectedEventId="e1" />);
    node = await screen.findByText("web").then((el) => el.closest("[data-node-id]")!);
    expect(node.getAttribute("data-node-state")).toBe("discovered");
    expect(node.querySelector(".tm-ring-amber")).toBeInTheDocument();

    // deselect — returns to latest (completed), no longer pulsing the rewind delta
    rerender(<TerrainMap runId="r1" selectedEventId={null} />);
    node = await screen.findByText("web").then((el) => el.closest("[data-node-id]")!);
    expect(node.getAttribute("data-node-state")).toBe("completed");
  });

  test("return-to-live: a time-travelling map surfaces a control that clears the selection", async () => {
    server.use(
      http.get("*/runs/r1/terrain2", () =>
        HttpResponse.json(
          terrain({
            groups: [{ id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
            nodes: [
              { id: "web", label: "web", group: "g", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
            ] as ResolvedTerrainV2["nodes"],
            updates: [
              { seq: 0, target_kind: "node", target_id: "web", event_id: "e1", state: "discovered", discovered: null, active: null },
              { seq: 1, target_kind: "node", target_id: "web", event_id: "e2", state: "completed", discovered: null, active: null },
            ],
          }),
        ),
      ),
    );
    const onReturnToLive = vi.fn();
    const { rerender } = renderWithProviders(
      <TerrainMap runId="r1" active selectedEventId={null} onReturnToLive={onReturnToLive} />,
    );
    await screen.findByText("web");
    // Live view (no selection): the map is already following, so no escape hatch is shown.
    expect(screen.queryByRole("button", { name: /return to live/i })).not.toBeInTheDocument();

    // Time-travelling to an earlier span: the map is frozen at that fold, so a discoverable
    // Return-to-live control appears; clicking it asks RunLive to clear the selection.
    rerender(<TerrainMap runId="r1" active selectedEventId="e1" onReturnToLive={onReturnToLive} />);
    const live = await screen.findByRole("button", { name: /return to live/i });
    fireEvent.click(live);
    expect(onReturnToLive).toHaveBeenCalledTimes(1);
  });

  test("hovering a node reports the event ids of updates targeting it; leave reports none", async () => {
    server.use(
      http.get("*/runs/r1/terrain2", () =>
        HttpResponse.json(
          terrain({
            groups: [{ id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
            nodes: [
              { id: "web", label: "web", group: "g", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "discovered" },
              { id: "db", label: "db", group: "g", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
            ] as ResolvedTerrainV2["nodes"],
            updates: [
              { seq: 0, target_kind: "node", target_id: "web", event_id: "e1", state: "discovered", discovered: null, active: null },
              { seq: 1, target_kind: "node", target_id: "db", event_id: "e2", state: "discovered", discovered: null, active: null },
            ],
          }),
        ),
      ),
    );
    const onHoverEvents = vi.fn();
    const { container } = renderWithProviders(
      <TerrainMap runId="r1" onHoverEvents={onHoverEvents} />,
    );
    await screen.findByText("web");
    const node = container.querySelector('[data-node-id="web"]')!;
    fireEvent.pointerEnter(node);
    expect(onHoverEvents).toHaveBeenCalledWith(["e1"]);
    fireEvent.pointerLeave(node);
    expect(onHoverEvents).toHaveBeenLastCalledWith([]);
  });

  test("shows the attribution-off notice only when a mission (segment) group is present", async () => {
    server.use(
      http.get("*/runs/r1/terrain2", () =>
        HttpResponse.json(
          terrain({
            groups: [{ id: "seg", label: "dmz", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
            nodes: [
              { id: "web", label: "web", group: "seg", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
            ] as ResolvedTerrainV2["nodes"],
          }),
        ),
      ),
    );
    renderWithProviders(<TerrainMap runId="r1" attributionOff />);
    expect(await screen.findByText(/attribution off/i)).toBeInTheDocument();
  });

  test("does NOT show the attribution-off notice when there is no mission (segment) group", async () => {
    server.use(
      http.get("*/runs/r1/terrain2", () =>
        HttpResponse.json(
          terrain({
            groups: [{ id: "agent", label: "Agent workspace", description: null, kind: "agent", order: 0, hidden: false, discovered: true }],
            nodes: [
              { id: "agent", label: "Agent", group: "agent", type: "agent", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
            ] as ResolvedTerrainV2["nodes"],
          }),
        ),
      ),
    );
    renderWithProviders(<TerrainMap runId="r1" attributionOff />);
    await screen.findByText("Agent workspace");
    expect(screen.queryByText(/attribution off/i)).not.toBeInTheDocument();
  });

  test("does NOT show the attribution-off notice when attributionOff is false", async () => {
    mount(
      terrain({
        groups: [{ id: "seg", label: "dmz", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
        nodes: [
          { id: "web", label: "web", group: "seg", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("web");
    expect(screen.queryByText(/attribution off/i)).not.toBeInTheDocument();
  });

  test("the legend can be minimised and expanded", async () => {
    mount(
      terrain({
        groups: [{ id: "g", label: "Group", description: null, kind: "agent", order: 0, hidden: false, discovered: true }],
        nodes: [
          { id: "agent", label: "Agent", group: "g", type: "agent", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("Agent");
    // expanded by default → the key rows are visible
    expect(screen.getByText("discovered")).toBeInTheDocument();
    // minimise → the key collapses to just the "Legend" pill (now an Expand control)
    fireEvent.click(screen.getByLabelText("Minimise legend"));
    expect(screen.queryByText("discovered")).not.toBeInTheDocument();
    // expand again → the key returns
    fireEvent.click(screen.getByLabelText("Expand legend"));
    expect(screen.getByText("discovered")).toBeInTheDocument();
  });

  test("fit reclaims the legend column when the legend is minimised (issue #23)", async () => {
    // Three nodes in one band → a WIDE graph, so the fit is width-limited and the legend
    // reserve directly shrinks the zoom (the regime where the bug rendered the whole graph
    // smaller than the viewport allowed).
    const { container } = mount(
      terrain({
        groups: [{ id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
        nodes: [
          { id: "a", label: "a", group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "b", label: "b", group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "c", label: "c", group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("a");
    const scale = () => {
      const layer = container.querySelector('[data-testid="terrain-svg"]')!.parentElement as HTMLElement;
      const m = /scale\(([\d.]+)\)/.exec(layer.style.transform || "");
      return m ? parseFloat(m[1]) : 1;
    };
    // jsdom reports 0×0, which makes fitToView bail — give the viewport a real size. A very
    // tall box keeps the fit width-limited regardless of the layout's exact aspect.
    const viewport = container.querySelector('[data-testid="terrain-viewport"]') as HTMLElement;
    Object.defineProperty(viewport, "clientWidth", { value: 600, configurable: true });
    Object.defineProperty(viewport, "clientHeight", { value: 5000, configurable: true });

    fireEvent.click(screen.getByLabelText("fit to view"));
    const withLegend = scale();
    // Minimising the legend refits automatically (the user hasn't panned/zoomed) and the
    // freed 132px column goes to the graph — the fit zoom grows.
    fireEvent.click(screen.getByLabelText("Minimise legend"));
    const withoutLegend = scale();
    expect(withoutLegend).toBeGreaterThan(withLegend);
    // Reopening reserves the column again so no node can land under the open legend.
    fireEvent.click(screen.getByLabelText("Expand legend"));
    expect(scale()).toBeLessThan(withoutLegend);
  });

  test("exposes zoom + fit controls", async () => {
    const { container } = mount(
      terrain({
        groups: [{ id: "g", label: "Group", description: null, kind: "agent", order: 0, hidden: false, discovered: true }],
        nodes: [
          { id: "agent", label: "Agent", group: "g", type: "agent", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    expect(await screen.findByLabelText("zoom in")).toBeInTheDocument();
    expect(await screen.findByLabelText("fit to view")).toBeInTheDocument();
    expect(await screen.findByLabelText("zoom out")).toBeInTheDocument();
    const viewport = container.querySelector('[data-testid="terrain-viewport"]');
    const card = screen.getByTestId("terrain-map-card");
    const canvas = screen.getByTestId("terrain-canvas");
    const footer = screen.getByTestId("terrain-footer");
    const controls = screen.getByTestId("terrain-view-controls");
    expect(card.className).toContain("min-h-[360px]");
    expect(canvas.className).toContain("min-h-0");
    expect(canvas.className).not.toContain("min-h-[360px]");
    expect(viewport).not.toContainElement(controls);
    expect(footer).toContainElement(controls);
    expect(controls.className).toContain("flex-wrap");
    expect(controls.className).not.toContain("absolute");
  });

  test("the + / − buttons actually change the map's zoom scale", async () => {
    const { container } = mount(
      terrain({
        groups: [{ id: "g", label: "Group", description: null, kind: "agent", order: 0, hidden: false, discovered: true }],
        nodes: [
          { id: "agent", label: "Agent", group: "g", type: "agent", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("Agent");
    // the transform layer wraps the svg; read its scale(...) factor
    const layer = () => container.querySelector('[data-testid="terrain-svg"]')!.parentElement as HTMLElement;
    const scaleOf = (el: HTMLElement) => {
      const m = /scale\(([\d.]+)\)/.exec(el.style.transform || "");
      return m ? parseFloat(m[1]) : 1;
    };
    const before = scaleOf(layer());
    fireEvent.click(screen.getByLabelText("zoom in"));
    const afterIn = scaleOf(layer());
    expect(afterIn).toBeGreaterThan(before);
    fireEvent.click(screen.getByLabelText("zoom out"));
    fireEvent.click(screen.getByLabelText("zoom out"));
    expect(scaleOf(layer())).toBeLessThan(afterIn);
  });

  test("shows an empty state before terrain resolves", async () => {
    mount(terrain({}));
    expect(await screen.findByText(/no terrain/i)).toBeInTheDocument();
  });

  test("hovering a service node with collapsed endpoint routes shows the routes tooltip", async () => {
    const { container } = mount(
      terrain({
        groups: [{ id: "infra", label: "XORCISE", description: null, kind: "infra", order: 0, hidden: false, discovered: true }],
        nodes: [
          { id: "hs", label: "hs", group: "infra", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "hs:register", label: "register", group: "infra", type: "endpoint", objective: false,
            description: null, discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "hs:join", label: "join", group: "infra", type: "endpoint", objective: false,
            description: null, discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("hs");
    // the collapsed endpoints are NOT drawn as their own nodes
    expect(container.querySelector('[data-node-id="hs:register"]')).not.toBeInTheDocument();
    expect(container.querySelector('[data-node-id="hs:join"]')).not.toBeInTheDocument();
    // no tooltip before hover
    expect(screen.queryByTestId("routes-tooltip")).not.toBeInTheDocument();

    const hsNode = container.querySelector('[data-node-id="hs"]')!;
    fireEvent.pointerEnter(hsNode);
    const tooltip = await screen.findByTestId("routes-tooltip");
    expect(tooltip).toHaveTextContent("register");
    expect(tooltip).toHaveTextContent("join");

    fireEvent.pointerLeave(hsNode);
    expect(screen.queryByTestId("routes-tooltip")).not.toBeInTheDocument();
  });

  test("an edge with a targeting note shows the route-used annotation persistently (no hover needed), and the bottom note caption is gone", async () => {
    server.use(
      http.get("*/runs/r1/terrain2", () =>
        HttpResponse.json(
          terrain({
            groups: [
              { id: "a", label: "A", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
              { id: "b", label: "B", description: null, kind: "segment", order: 1, hidden: false, discovered: true },
            ],
            nodes: [
              { id: "n1", label: "n1", group: "a", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
              { id: "n2", label: "n2", group: "b", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
            ] as ResolvedTerrainV2["nodes"],
            edges: [{ id: "e1", src: "n1", dst: "n2", label: null, active: true }],
            updates: [
              { seq: 0, target_kind: "edge", target_id: "e1", event_id: "e1ev", active: true, discovered: null,
                state: null, note: "used the SSRF pivot" },
            ],
          }),
        ),
      ),
    );
    renderWithProviders(<TerrainMap runId="r1" />);
    await screen.findByText("n1");

    // note renders on the edge WITHOUT any hover interaction
    const tooltip = await screen.findByTestId("edge-note-tooltip");
    expect(tooltip).toHaveTextContent("used the SSRF pivot");

    // the old bottom-of-panel caption is gone entirely
    expect(screen.queryByTestId("terrain-note")).not.toBeInTheDocument();
  });

  test("a long edge note is height-clamped and truncated, with the full text kept for hover-expand", async () => {
    server.use(
      http.get("*/runs/r1/terrain2", () =>
        HttpResponse.json(
          terrain({
            groups: [
              { id: "a", label: "A", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
              { id: "b", label: "B", description: null, kind: "segment", order: 1, hidden: false, discovered: true },
            ],
            nodes: [
              { id: "n1", label: "n1", group: "a", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
              { id: "n2", label: "n2", group: "b", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
            ] as ResolvedTerrainV2["nodes"],
            edges: [{ id: "e1", src: "n1", dst: "n2", label: null, active: true }],
            updates: [
              { seq: 0, target_kind: "edge", target_id: "e1", event_id: "e1ev", active: true, discovered: null,
                state: null, note: "SSRF pivot via /fetch to the internal inventory service on 8080" },
            ],
          }),
        ),
      ),
    );
    renderWithProviders(<TerrainMap runId="r1" />);
    await screen.findByText("n1");
    const tooltip = await screen.findByTestId("edge-note-tooltip");
    // the full note lives in the DOM (accessible / available to reveal), every word survives
    expect(tooltip.textContent).toContain("SSRF");
    expect(tooltip.textContent).toContain("8080");
    // a note longer than the collapsed height is marked truncated, with clamp/expand heights wired
    expect(tooltip.getAttribute("data-truncated")).toBe("true");
    const box = tooltip.querySelector(".tm-edge-note")!;
    expect(box.className).toContain("tm-edge-note--truncated");
    const style = box.getAttribute("style") ?? "";
    expect(style).toContain("--tm-note-collapsed");
    expect(style).toContain("--tm-note-full");
  });

  test("a short edge note is not truncated (no clamp/expand affordance)", async () => {
    server.use(
      http.get("*/runs/r1/terrain2", () =>
        HttpResponse.json(
          terrain({
            groups: [
              { id: "a", label: "A", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
              { id: "b", label: "B", description: null, kind: "segment", order: 1, hidden: false, discovered: true },
            ],
            nodes: [
              { id: "n1", label: "n1", group: "a", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
              { id: "n2", label: "n2", group: "b", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
            ] as ResolvedTerrainV2["nodes"],
            edges: [{ id: "e1", src: "n1", dst: "n2", label: null, active: true }],
            updates: [
              { seq: 0, target_kind: "edge", target_id: "e1", event_id: "e1ev", active: true, discovered: null,
                state: null, note: "found the pivot" },
            ],
          }),
        ),
      ),
    );
    renderWithProviders(<TerrainMap runId="r1" />);
    await screen.findByText("n1");
    const tooltip = await screen.findByTestId("edge-note-tooltip");
    expect(tooltip.textContent).toContain("found the pivot");
    expect(tooltip.getAttribute("data-truncated")).toBe("false");
    expect(tooltip.querySelector(".tm-edge-note")!.className).not.toContain("tm-edge-note--truncated");
  });

  test("an edge with no targeting note shows no route-used annotation", async () => {
    const { container } = mount(
      terrain({
        groups: [
          { id: "a", label: "A", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
          { id: "b", label: "B", description: null, kind: "segment", order: 1, hidden: false, discovered: true },
        ],
        nodes: [
          { id: "n1", label: "n1", group: "a", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "n2", label: "n2", group: "b", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
        edges: [{ id: "e1", src: "n1", dst: "n2", label: null, active: false }],
      }),
    );
    await screen.findByText("n1");
    expect(container.querySelector('[data-edge-id="e1"]')).toBeInTheDocument();
    expect(screen.queryByTestId("edge-note-tooltip")).not.toBeInTheDocument();
  });

  test("time-travel: an edge's note reflects the selected earlier span, not the latest", async () => {
    server.use(
      http.get("*/runs/r1/terrain2", () =>
        HttpResponse.json(
          terrain({
            groups: [
              { id: "a", label: "A", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
              { id: "b", label: "B", description: null, kind: "segment", order: 1, hidden: false, discovered: true },
            ],
            nodes: [
              { id: "n1", label: "n1", group: "a", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
              { id: "n2", label: "n2", group: "b", type: "service", objective: false, description: null,
                discovery_condition: null, completion_condition: null, state: "defined" },
            ] as ResolvedTerrainV2["nodes"],
            edges: [{ id: "e1", src: "n1", dst: "n2", label: null, active: true }],
            updates: [
              { seq: 0, target_kind: "edge", target_id: "e1", event_id: "e1ev", active: true, discovered: null,
                state: null, note: "found the pivot" },
              { seq: 1, target_kind: "edge", target_id: "e1", event_id: "e2ev", active: true, discovered: null,
                state: null, note: "used the SSRF pivot" },
            ],
          }),
        ),
      ),
    );
    const { rerender } = renderWithProviders(<TerrainMap runId="r1" selectedEventId={null} />);
    await screen.findByText("n1");
    expect(await screen.findByTestId("edge-note-tooltip")).toHaveTextContent("used the SSRF pivot");

    rerender(<TerrainMap runId="r1" selectedEventId="e1ev" />);
    expect(await screen.findByTestId("edge-note-tooltip")).toHaveTextContent("found the pivot");

    rerender(<TerrainMap runId="r1" selectedEventId={null} />);
    expect(await screen.findByTestId("edge-note-tooltip")).toHaveTextContent("used the SSRF pivot");
  });

  test("two edges activated by different updates are BOTH active/highlighted at the latest fold, not just the most recent", async () => {
    const { container } = mount(
      terrain({
        groups: [
          { id: "a", label: "A", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
          { id: "b", label: "B", description: null, kind: "segment", order: 1, hidden: false, discovered: true },
          { id: "c", label: "C", description: null, kind: "segment", order: 2, hidden: false, discovered: true },
        ],
        nodes: [
          { id: "n1", label: "n1", group: "a", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "n2", label: "n2", group: "b", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "n3", label: "n3", group: "c", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
        // both edges start inactive; two SEPARATE updates flip each one active in turn, then a
        // third (unrelated) update moves the pulse boundary off both edges — isolating "active"
        // (latched, BLUE) from "probing" (the delta, YELLOW) so this test only proves the fold
        // doesn't clobber an earlier edge's active flag with a later one's.
        edges: [
          { id: "e1", src: "n1", dst: "n2", label: null, active: false },
          { id: "e2", src: "n2", dst: "n3", label: null, active: false },
        ],
        updates: [
          { seq: 0, target_kind: "edge", target_id: "e1", event_id: "e1ev", active: true, discovered: null, state: null },
          { seq: 1, target_kind: "edge", target_id: "e2", event_id: "e2ev", active: true, discovered: null, state: null },
          { seq: 2, target_kind: "node", target_id: "n1", event_id: "e3ev", state: "discovered", discovered: null, active: null },
        ],
      }),
    );
    await screen.findByText("n1");
    // latest fold: BOTH edges latch active, not just e2 (the most-recently-touched one) — the
    // final update's pulse lands on node n1, so neither edge is the delta here, isolating the
    // "stays highlighted" assertion from the separate pulse/AMBER behavior.
    const e1 = container.querySelector('[data-edge-id="e1"]')!;
    const e2 = container.querySelector('[data-edge-id="e2"]')!;
    expect(e1.getAttribute("data-edge-active")).toBe("true");
    expect(e1.getAttribute("stroke")).toBe("var(--color-terrain-cool)");
    expect(e2.getAttribute("data-edge-active")).toBe("true");
    expect(e2.getAttribute("stroke")).toBe("var(--color-terrain-cool)");
  });

  test("a hidden/undiscovered group renders (dashed/grey box present), not absent", async () => {
    const { container } = mount(
      terrain({
        groups: [
          { id: "visible", label: "Visible", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
          { id: "hidden", label: "Hidden", description: null, kind: "segment", order: 1, hidden: true, discovered: false },
        ],
        nodes: [
          { id: "n1", label: "n1", group: "visible", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "n2", label: "n2", group: "hidden", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("n1");
    // the hidden group's box IS present (greyed, not omitted) and its member node is drawn
    const box = container.querySelector('[data-group-id="hidden"]')!;
    expect(box).toBeInTheDocument();
    expect(box.getAttribute("data-group-discovered")).toBe("false");
    const rect = box.querySelector("rect")!;
    expect(rect.getAttribute("stroke-dasharray")).toBeTruthy();
    expect(container.querySelector('[data-node-id="n2"]')).toBeInTheDocument();
  });

  test("hovering a node with a description (and no routes) shows the description in the tooltip", async () => {
    const { container } = mount(
      terrain({
        groups: [{ id: "seg", label: "DMZ", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
        nodes: [
          { id: "web", label: "web", group: "seg", type: "service", objective: false,
            description: "DMZ web app with an SSRF-capable /fetch endpoint.",
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("web");
    expect(screen.queryByTestId("node-tooltip")).not.toBeInTheDocument();

    const webNode = container.querySelector('[data-node-id="web"]')!;
    fireEvent.pointerEnter(webNode);
    const tooltip = await screen.findByTestId("node-tooltip");
    expect(tooltip).toHaveTextContent("DMZ web app with an SSRF-capable /fetch endpoint.");
    // no routes for this node, so the routes section should not be present
    expect(screen.queryByTestId("routes-tooltip")).not.toBeInTheDocument();

    fireEvent.pointerLeave(webNode);
    expect(screen.queryByTestId("node-tooltip")).not.toBeInTheDocument();
  });

  test("a node with neither description nor routes shows no tooltip on hover", async () => {
    const { container } = mount(
      terrain({
        groups: [{ id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
        nodes: [
          { id: "web", label: "web", group: "g", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
      }),
    );
    await screen.findByText("web");
    const webNode = container.querySelector('[data-node-id="web"]')!;
    fireEvent.pointerEnter(webNode);
    expect(screen.queryByTestId("node-tooltip")).not.toBeInTheDocument();
  });

  test("an active edge renders in the blue stroke; an inactive edge stays border-grey", async () => {
    const { container } = mount(
      terrain({
        groups: [
          { id: "a", label: "A", description: null, kind: "segment", order: 0, hidden: false, discovered: true },
          { id: "b", label: "B", description: null, kind: "segment", order: 1, hidden: false, discovered: true },
        ],
        nodes: [
          { id: "n1", label: "n1", group: "a", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "n2", label: "n2", group: "b", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "n3", label: "n3", group: "a", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
        ] as ResolvedTerrainV2["nodes"],
        edges: [
          { id: "e1", src: "n1", dst: "n2", label: null, active: true },
          { id: "e2", src: "n1", dst: "n3", label: null, active: false },
        ],
      }),
    );
    await screen.findByText("n1");
    const e1 = container.querySelector('[data-edge-id="e1"]')!;
    expect(e1.getAttribute("data-edge-active")).toBe("true");
    expect(e1.getAttribute("stroke")).toBe("var(--color-terrain-cool)");

    const e2 = container.querySelector('[data-edge-id="e2"]')!;
    expect(e2.getAttribute("data-edge-active")).toBe("false");
    expect(e2.getAttribute("stroke")).toBe("var(--color-border)");
  });

  test("probing a downstream node lights the WHOLE path back to the agent in yellow", async () => {
    const { container } = mount(
      terrain({
        groups: [
          { id: "aw", label: "Agent workspace", description: null, kind: "agent", order: 0, hidden: false, discovered: true },
          { id: "dmz", label: "DMZ", description: null, kind: "segment", order: 1, hidden: false, discovered: true },
          { id: "int", label: "Internal", description: null, kind: "segment", order: 2, hidden: false, discovered: true },
        ],
        nodes: [
          { id: "agent", label: "Agent", group: "aw", type: "agent", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "defined" },
          { id: "web", label: "web", group: "dmz", type: "service", objective: false, description: null,
            discovery_condition: null, completion_condition: null, state: "discovered" },
          { id: "internal", label: "internal", group: "int", type: "service", objective: true, description: null,
            discovery_condition: null, completion_condition: null, state: "discovered" },
        ] as ResolvedTerrainV2["nodes"],
        edges: [
          { id: "e-agent-web", src: "agent", dst: "web", label: null, active: true },
          { id: "e-web-internal", src: "web", dst: "internal", label: null, active: true },
        ],
        // the latest delta is the agent probing `internal` (both updates share the event)
        updates: [
          { seq: 0, target_kind: "edge", target_id: "e-web-internal", event_id: "ev", active: true, state: null, discovered: null },
          { seq: 1, target_kind: "node", target_id: "internal", event_id: "ev", state: "discovered", active: null, discovered: null },
        ],
        objective_id: "internal",
      }),
    );
    await screen.findByText("internal");
    // NOT just the last edge — the full agent→web→internal path lights yellow (probing)
    expect(container.querySelector('[data-edge-id="e-web-internal"]')!.getAttribute("stroke")).toBe("var(--color-terrain-hot)");
    expect(container.querySelector('[data-edge-id="e-agent-web"]')!.getAttribute("stroke")).toBe("var(--color-terrain-hot)");
  });

  describe("wheel gating (page-scroll hijack fix)", () => {
    async function mountMap() {
      const { container } = mount(
        terrain({
          groups: [{ id: "g", label: "Group", description: null, kind: "agent", order: 0, hidden: false, discovered: true }],
          nodes: [
            { id: "agent", label: "Agent", group: "g", type: "agent", objective: false, description: null,
              discovery_condition: null, completion_condition: null, state: "defined" },
          ] as ResolvedTerrainV2["nodes"],
        }),
      );
      await screen.findByText("Agent");
      const viewport = container.querySelector('[data-testid="terrain-viewport"]') as HTMLElement;
      // jsdom has no pointer capture; the pan onPointerDown calls it on the container
      viewport.setPointerCapture = () => {};
      const scale = () => {
        const layer = container.querySelector('[data-testid="terrain-svg"]')!.parentElement as HTMLElement;
        const m = /scale\(([\d.]+)\)/.exec(layer.style.transform || "");
        return m ? parseFloat(m[1]) : 1;
      };
      return { viewport, scale };
    }

    // NATIVE dispatch — the component's wheel handler is a non-passive native listener (React's
    // root-delegated onWheel is passive, so it could never preventDefault), so fireEvent-style
    // synthetic paths aren't the surface under test.
    function wheel(el: Element, init: WheelEventInit = {}) {
      const ev = new WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: -100, ...init });
      act(() => {
        el.dispatchEvent(ev);
      });
      return ev;
    }

    test("plain wheel over an inactive map does NOT zoom, lets the page scroll, and shows the tip", async () => {
      const { viewport, scale } = await mountMap();
      const before = scale();
      const ev = wheel(viewport);
      expect(scale()).toBe(before);
      expect(ev.defaultPrevented).toBe(false); // page scroll proceeds
      expect(screen.getByTestId("wheel-tip")).toHaveTextContent(/scroll to zoom/i);
    });

    test("ctrl+wheel zooms immediately (no activation needed) and consumes the event", async () => {
      const { viewport, scale } = await mountMap();
      const before = scale();
      const ev = wheel(viewport, { ctrlKey: true });
      expect(scale()).toBeGreaterThan(before);
      expect(ev.defaultPrevented).toBe(true);
      expect(screen.queryByTestId("wheel-tip")).not.toBeInTheDocument();
    });

    test("cmd (meta)+wheel zooms too", async () => {
      const { viewport, scale } = await mountMap();
      const before = scale();
      wheel(viewport, { metaKey: true, deltaY: 100 });
      expect(scale()).toBeLessThan(before);
    });

    test("plain wheel zooms after the map is activated by pointerdown", async () => {
      const { viewport, scale } = await mountMap();
      fireEvent.pointerDown(viewport);
      const before = scale();
      const ev = wheel(viewport);
      expect(scale()).toBeGreaterThan(before);
      expect(ev.defaultPrevented).toBe(true);
    });

    test("blur deactivates: plain wheel goes back to page scroll + tip", async () => {
      const { viewport, scale } = await mountMap();
      fireEvent.pointerDown(viewport);
      fireEvent.pointerUp(viewport);
      wheel(viewport); // activated: zooms
      const zoomed = scale();
      expect(zoomed).toBeGreaterThan(1);
      fireEvent.blur(viewport);
      const ev = wheel(viewport);
      expect(scale()).toBe(zoomed); // gating restored: no zoom
      expect(ev.defaultPrevented).toBe(false);
      expect(screen.getByTestId("wheel-tip")).toBeInTheDocument();
    });

    test("Escape deactivates plain-wheel zoom", async () => {
      const { viewport, scale } = await mountMap();
      fireEvent.pointerDown(viewport);
      wheel(viewport);
      const zoomed = scale();
      expect(zoomed).toBeGreaterThan(1);
      fireEvent.keyDown(viewport, { key: "Escape" });
      const ev = wheel(viewport);
      expect(scale()).toBe(zoomed);
      expect(ev.defaultPrevented).toBe(false);
    });
  });

  describe("fullscreen toggle (expandable)", () => {
    const graph = terrain({
      groups: [{ id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
      nodes: [
        { id: "web", label: "web", group: "g", type: "service", objective: false, description: null,
          discovery_condition: null, completion_condition: null, state: "defined" },
      ] as ResolvedTerrainV2["nodes"],
    });

    test("no fullscreen control unless expandable is passed", async () => {
      server.use(http.get("*/runs/r1/terrain2", () => HttpResponse.json(graph)));
      renderWithProviders(<TerrainMap runId="r1" />);
      await screen.findByText("web");
      expect(screen.queryByRole("button", { name: "fullscreen" })).toBeNull();
    });

    test("expands into a modal overlay, and the toggle collapses it again", async () => {
      server.use(http.get("*/runs/r1/terrain2", () => HttpResponse.json(graph)));
      renderWithProviders(<TerrainMap runId="r1" expandable />);
      await screen.findByText("web");
      expect(screen.queryByRole("dialog")).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "fullscreen" }));
      const overlay = screen.getByRole("dialog");
      expect(overlay).toBeInTheDocument();
      expect(overlay.className).toContain("fixed");
      // SAME instance re-homed, not a second map: still exactly one SVG.
      expect(screen.getAllByTestId("terrain-svg")).toHaveLength(1);

      fireEvent.click(screen.getByRole("button", { name: "exit fullscreen" }));
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    test("Escape closes the expanded overlay", async () => {
      server.use(http.get("*/runs/r1/terrain2", () => HttpResponse.json(graph)));
      renderWithProviders(<TerrainMap runId="r1" expandable />);
      await screen.findByText("web");
      fireEvent.click(screen.getByRole("button", { name: "fullscreen" }));
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      fireEvent.keyDown(window, { key: "Escape" });
      expect(screen.queryByRole("dialog")).toBeNull();
      // the map itself is still there, back in its pane
      expect(screen.getByTestId("terrain-svg")).toBeInTheDocument();
    });

    test("the expanded overlay portals to document.body, escaping transformed ancestors", async () => {
      server.use(http.get("*/runs/r1/terrain2", () => HttpResponse.json(graph)));
      renderWithProviders(<TerrainMap runId="r1" expandable />);
      await screen.findByText("web");
      fireEvent.click(screen.getByRole("button", { name: "fullscreen" }));
      // REGRESSION: the narrow layout mounts the map under an ancestor that animates
      // `transform` (TabsContent's fade-up entrance) — `fixed` positions against such an
      // ancestor, not the viewport, so anything short of a body-level portal pins the
      // "fullscreen" overlay inside the Terrain pane.
      expect(screen.getByRole("dialog").parentElement).toBe(document.body);
    });

    test("wheel zoom still works after a fullscreen round-trip (re-created viewport re-binds)", async () => {
      server.use(http.get("*/runs/r1/terrain2", () => HttpResponse.json(graph)));
      renderWithProviders(<TerrainMap runId="r1" expandable />);
      await screen.findByText("web");
      fireEvent.click(screen.getByRole("button", { name: "fullscreen" }));
      fireEvent.click(screen.getByRole("button", { name: "exit fullscreen" }));
      // The portal toggle re-creates the viewport ELEMENT; the native wheel listener must
      // re-attach to it (for a terminal run `layout` never changes to re-run the attach).
      const viewport = screen.getByTestId("terrain-viewport");
      const scale = () => {
        const layer = screen.getByTestId("terrain-svg").parentElement as HTMLElement;
        const m = /scale\(([\d.]+)\)/.exec(layer.style.transform || "");
        return m ? parseFloat(m[1]) : 1;
      };
      const before = scale();
      act(() => {
        viewport.dispatchEvent(
          new WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: -100, ctrlKey: true }),
        );
      });
      expect(scale()).toBeGreaterThan(before);
    });

    test("clicking the backdrop closes; clicking inside the card does not", async () => {
      server.use(http.get("*/runs/r1/terrain2", () => HttpResponse.json(graph)));
      renderWithProviders(<TerrainMap runId="r1" expandable />);
      await screen.findByText("web");
      fireEvent.click(screen.getByRole("button", { name: "fullscreen" }));
      const overlay = screen.getByRole("dialog");
      fireEvent.click(screen.getByTestId("terrain-viewport")); // inside the card
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      fireEvent.click(overlay); // the backdrop itself
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

});
