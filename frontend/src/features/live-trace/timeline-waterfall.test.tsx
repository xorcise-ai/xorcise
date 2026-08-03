import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { AgentEvent } from "@/lib/api/types";
import { TimelineWaterfall } from "./timeline-waterfall";

function ev(overrides: Partial<AgentEvent> = {}): AgentEvent {
  return {
    run_id: "r",
    id: overrides.id ?? "s1",
    ts: overrides.ts ?? new Date(1000).toISOString(),
    source_agent: "generic",
    kind: overrides.kind ?? "terminal_command",
    role: "agent",
    title: overrides.title ?? "exec",
    body: "",
    data: {},
    severity: "info",
    raw_ref: {
      run_id: "r",
      raw_seq: 0,
      span_id: overrides.id ?? "s1",
      signal: "trace",
    },
    ...overrides,
  };
}

const leftOf = (el: HTMLElement) => parseFloat(el.style.left);
const widthOf = (el: HTMLElement) => parseFloat(el.style.width);

describe("TimelineWaterfall (AgentEvent)", () => {
  it("shows an empty state when there are no events", () => {
    render(<TimelineWaterfall events={[]} />);
    expect(screen.getByText(/no timeline/i)).toBeInTheDocument();
  });

  it("renders one bar per event, coloured by kind via KIND_META.dotClass", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "a", kind: "terminal_command", ts: new Date(1000).toISOString() }),
          ev({ id: "b", kind: "message", ts: new Date(2000).toISOString() }),
        ]}
      />,
    );
    const bars = screen.getAllByTestId("timeline-bar");
    expect(bars).toHaveLength(2);
    expect(bars[0].className).toContain("bg-terminal");
    expect(bars[1].className).toContain("bg-assistant"); // message (role agent) → assistant blue
  });

  it("lays events out as a waterfall with increasing offsets over time", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "a", ts: new Date(1000).toISOString() }),
          ev({ id: "b", ts: new Date(2000).toISOString() }),
          ev({ id: "c", ts: new Date(3000).toISOString() }),
        ]}
      />,
    );
    const bars = screen.getAllByTestId("timeline-bar");
    expect(leftOf(bars[0])).toBeLessThan(leftOf(bars[1]));
    expect(leftOf(bars[1])).toBeLessThan(leftOf(bars[2]));
  });

  it("renders every event on ONE timeline line (single track, regardless of agent)", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "a", actor_name: "solver", ts: new Date(1000).toISOString() }),
          ev({ id: "b", actor_name: "solver", ts: new Date(2000).toISOString() }),
          ev({ id: "c", actor_name: "analyzer", ts: new Date(3000).toISOString() }),
        ]}
      />,
    );
    // One single line — not one row per agent/turn — with a marker per event.
    expect(screen.getAllByTestId("timeline-lane")).toHaveLength(1);
    expect(screen.getAllByTestId("timeline-bar")).toHaveLength(3);
  });

  it("keeps every event as its own marker on the one line", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "a", group_id: "t1", ts: new Date(1000).toISOString() }),
          ev({ id: "b", group_id: "t2", ts: new Date(2000).toISOString() }),
          ev({ id: "c", group_id: "t3", ts: new Date(3000).toISOString() }),
        ]}
      />,
    );
    expect(screen.getAllByTestId("timeline-lane")).toHaveLength(1);
    expect(screen.getAllByTestId("timeline-bar")).toHaveLength(3);
  });

  it("a COT message and a same-instant shell command are BOTH present as point markers (neither hidden)", () => {
    const t = new Date(5000).toISOString();
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "cot", kind: "message", role: "agent", ts: t }),
          ev({ id: "sh", kind: "terminal_command", ts: t }),
          ev({ id: "cot2", kind: "message", role: "agent", ts: new Date(20000).toISOString() }),
        ]}
      />,
    );
    // One line, but every event is an instantaneous point tick (never a gap-filled bar), so the two
    // same-timestamp events are two adjacent marks — not one collapsing behind the other.
    expect(screen.getAllByTestId("timeline-lane")).toHaveLength(1);
    const bars = screen.getAllByTestId("timeline-bar");
    expect(bars).toHaveLength(3);
    // both COT marks and the shell mark are present, each coloured by its family
    expect(bars.filter((b) => b.className.includes("bg-assistant"))).toHaveLength(2); // both COT
    expect(bars.filter((b) => b.className.includes("bg-terminal"))).toHaveLength(1); // the shell
  });

  it("hovering a marker reveals what the event was (title carries the display label + time)", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "tool", kind: "tool_call", title: "curl", ts: "2026-07-03T10:00:00Z" }),
        ]}
      />,
    );
    const mark = screen.getByTestId("timeline-bar");
    expect(mark.getAttribute("title")).toContain("Agent curl"); // tool_call display label
    expect(mark.getAttribute("title")).toMatch(/\d{2}:\d{2}:\d{2}/); // its time
  });

  it("plots infra activities as milestone markers, so the timeline carries every Trace row", () => {
    const onSelectEvent = vi.fn();
    render(
      <TimelineWaterfall
        events={[ev({ id: "a", kind: "terminal_command", ts: new Date(1000).toISOString() })]}
        infraRows={[
          { id: "infra:3", ts: new Date(2000).toISOString(), label: "Agent joined the tailnet", targetId: "m:hs", seq: 3 },
        ]}
        onSelectEvent={onSelectEvent}
      />,
    );
    // the agent event is a dash-tick; the infra activity is a distinct milestone marker
    expect(screen.getAllByTestId("timeline-bar")).toHaveLength(1);
    const infra = screen.getByTestId("timeline-infra");
    expect(infra.getAttribute("title")).toContain("Agent joined the tailnet");
    // it names itself in the legend, and clicking it selects it (drives terrain time-travel)
    expect(screen.getByText("Infra")).toBeInTheDocument();
    fireEvent.click(infra);
    expect(onSelectEvent).toHaveBeenCalledWith("infra:3");
  });

  it("skips an anchorless (ts-less) infra activity — it has no place on the time scale", () => {
    render(
      <TimelineWaterfall
        events={[ev({ id: "a", ts: new Date(1000).toISOString() })]}
        infraRows={[{ id: "infra:1", ts: null, label: "anchorless", targetId: "m:x", seq: 1 }]}
      />,
    );
    expect(screen.queryByTestId("timeline-infra")).not.toBeInTheDocument();
  });

  it("renders the timeline from infra activities alone (no agent events yet)", () => {
    render(
      <TimelineWaterfall
        events={[]}
        infraRows={[
          { id: "infra:1", ts: new Date(1000).toISOString(), label: "Fetched the run brief", targetId: "m:rc", seq: 1 },
        ]}
      />,
    );
    // not the empty state — the one infra milestone is on the line
    expect(screen.queryByText(/no timeline/i)).not.toBeInTheDocument();
    expect(screen.getAllByTestId("timeline-infra")).toHaveLength(1);
  });

  it("does not collapse to a single dot when all timestamps are equal", () => {
    const t = new Date(1000).toISOString();
    render(
      <TimelineWaterfall
        events={[ev({ id: "a", ts: t }), ev({ id: "b", ts: t }), ev({ id: "c", ts: t })]}
      />,
    );
    const bars = screen.getAllByTestId("timeline-bar");
    expect(bars).toHaveLength(3);
    // Not all at the same left → visibly distinct, not one stacked dot.
    const lefts = new Set(bars.map(leftOf));
    expect(lefts.size).toBeGreaterThan(1);
  });

  it("labels the bars with a legend of the colour families present, deduped by colour", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "a", kind: "file_edit", ts: new Date(1000).toISOString() }), // bg-file
          ev({ id: "b", kind: "file_read", ts: new Date(2000).toISOString() }), // bg-file (same family)
          ev({ id: "c", kind: "message", ts: new Date(3000).toISOString() }), // bg-assistant (role agent)
        ]}
      />,
    );
    const legend = screen.getByTestId("timeline-legend");
    const swatches = within(legend).getAllByTestId("legend-swatch");
    // file_edit + file_read share the "file" colour → collapse to ONE family, so 2 not 3.
    expect(swatches).toHaveLength(2);
    // No colour is repeated in the legend (the reviewer's complaint).
    const colours = swatches.map((s) => s.className.match(/bg-[\w-]+/)?.[0]);
    expect(new Set(colours).size).toBe(colours.length);
    // Families are named, not just coloured — in the operator vocabulary.
    expect(legend).toHaveTextContent(/file/i);
    expect(legend).toHaveTextContent(/agent cot/i);
  });

  it("gives the user prompt and assistant message distinct legend swatches", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "u", kind: "message", role: "user", ts: new Date(1000).toISOString() }),
          ev({ id: "a", kind: "message", role: "agent", ts: new Date(2000).toISOString() }),
        ]}
      />,
    );
    const legend = screen.getByTestId("timeline-legend");
    const colours = within(legend)
      .getAllByTestId("legend-swatch")
      .map((s) => s.className.match(/bg-[\w-]+/)?.[0]);
    expect(colours).toContain("bg-user"); // user prompt = gold
    expect(colours).toContain("bg-assistant"); // assistant = blue
  });

  it("excludes debug-group events (metric/unknown) from the lanes and the legend", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "a", kind: "terminal_command", ts: new Date(1000).toISOString() }),
          ev({ id: "m", kind: "metric", ts: new Date(2000).toISOString() }),
          ev({ id: "u", kind: "unknown", ts: new Date(3000).toISOString() }),
          ev({ id: "b", kind: "message", ts: new Date(4000).toISOString() }),
        ]}
      />,
    );
    // Only the two meaningful events get bars; the grey metric/unknown noise is filtered out.
    expect(screen.getAllByTestId("timeline-bar")).toHaveLength(2);
    // …and the legend loses the grey swatch too (terminal + assistant only).
    const legend = screen.getByTestId("timeline-legend");
    expect(within(legend).getAllByTestId("legend-swatch")).toHaveLength(2);
  });

  it("shows the empty state when every event is debug-group noise", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "m", kind: "metric", ts: new Date(1000).toISOString() }),
          ev({ id: "u", kind: "unknown", ts: new Date(2000).toISOString() }),
        ]}
      />,
    );
    expect(screen.getByText(/no timeline/i)).toBeInTheDocument();
    expect(screen.queryAllByTestId("timeline-bar")).toHaveLength(0);
  });

  it("uses duration_ms for the bar width when present", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "a", ts: new Date(1000).toISOString(), duration_ms: 4000 }),
          ev({ id: "b", ts: new Date(10000).toISOString() }),
        ]}
      />,
    );
    const bars = screen.getAllByTestId("timeline-bar");
    // The first bar spans 4s of a ~9s window → a clearly non-trivial width.
    expect(widthOf(bars[0])).toBeGreaterThan(5);
  });

  it("clicking a bar selects its event via onSelectEvent", () => {
    const onSelectEvent = vi.fn();
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "a", ts: new Date(1000).toISOString() }),
          ev({ id: "b", ts: new Date(2000).toISOString() }),
        ]}
        onSelectEvent={onSelectEvent}
      />,
    );
    fireEvent.click(screen.getAllByTestId("timeline-bar")[1]);
    expect(onSelectEvent).toHaveBeenCalledWith("b");
    expect(onSelectEvent).toHaveBeenCalledTimes(1);
  });

  it("rings the selected bar and marks it data-selected", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "a", ts: new Date(1000).toISOString() }),
          ev({ id: "b", ts: new Date(2000).toISOString() }),
        ]}
        selectedEventId="b"
      />,
    );
    const bars = screen.getAllByTestId("timeline-bar");
    expect(bars[0].getAttribute("data-selected")).toBeNull();
    expect(bars[1].getAttribute("data-selected")).toBe("true");
    expect(bars[1].className).toContain("ring-primary");
    expect(bars[0].className).not.toContain("ring-primary");
  });

  it("renders a time-axis ruler with elapsed tick labels starting at 0:00", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({ id: "a", ts: new Date(0).toISOString() }),
          ev({ id: "b", ts: new Date(150_000).toISOString() }), // 2m30s span
        ]}
      />,
    );
    const axis = screen.getByTestId("timeline-axis");
    const ticks = within(axis).getAllByTestId("timeline-tick");
    // ~4-6 ticks with sensible bounds, positioned along the same % scale as the bars.
    expect(ticks.length).toBeGreaterThanOrEqual(4);
    expect(ticks.length).toBeLessThanOrEqual(7);
    expect(axis).toHaveTextContent("0:00");
    expect(axis).toHaveTextContent("0:30");
    expect(leftOf(ticks[0])).toBe(0);
    expect(leftOf(ticks[1])).toBeGreaterThan(0);
  });

  it("skips the axis when the span is degenerate (all timestamps equal)", () => {
    const t = new Date(1000).toISOString();
    render(<TimelineWaterfall events={[ev({ id: "a", ts: t }), ev({ id: "b", ts: t })]} />);
    expect(screen.queryByTestId("timeline-axis")).not.toBeInTheDocument();
  });

  it("tooltips carry the display label plus the event time", () => {
    render(
      <TimelineWaterfall
        events={[
          ev({
            id: "a",
            kind: "terminal_command",
            title: "terminal",
            ts: "2026-07-03T10:00:00Z",
            duration_ms: 2000,
          }),
          ev({ id: "b", kind: "message", ts: "2026-07-03T10:01:00Z" }),
        ]}
      />,
    );
    const bar = screen.getAllByTestId("timeline-bar")[0];
    expect(bar.getAttribute("title")).toContain("Agent Terminal");
    expect(bar.getAttribute("title")).toMatch(/\d{2}:\d{2}:\d{2}/);
    expect(bar.getAttribute("title")).toContain("2s");
  });
});
