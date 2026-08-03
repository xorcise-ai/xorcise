import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import type { AgentEvent, AgentEventKind } from "@/lib/api/types";
import { ReplayTimeline } from "./replay-timeline";

let seq = 0;
function agentEvent(overrides: Partial<AgentEvent> = {}): AgentEvent {
  seq += 1;
  return {
    run_id: "r1",
    id: `evt-${seq}`,
    ts: new Date(seq * 1000).toISOString(),
    source_agent: "generic",
    kind: "message",
    role: "agent",
    title: `event-${seq}`,
    body: `body-${seq}`,
    data: {},
    severity: "info",
    raw_ref: {
      run_id: "r1",
      raw_seq: seq,
      span_id: `s${seq}`,
      signal: "trace",
    },
    ...overrides,
  };
}

/** A controllable ResizeObserver stand-in — jsdom has none, and the follow-tail re-pin is now
 *  driven by observing the scroll content's SIZE (not the entry count). Tests install this, then
 *  fire the captured callback to simulate an existing card growing (merged terminal output, a
 *  same-group Claude burst) without any change to the entry count. */
class MockResizeObserver {
  static instances: MockResizeObserver[] = [];
  cb: ResizeObserverCallback;
  constructor(cb: ResizeObserverCallback) {
    this.cb = cb;
    MockResizeObserver.instances.push(this);
  }
  observe() {}
  unobserve() {}
  disconnect() {}
  fire() {
    this.cb([], this as unknown as ResizeObserver);
  }
}

const ALL_KINDS: AgentEventKind[] = [
  "message",
  "thinking",
  "terminal_command",
  "terminal_output",
  "file_edit",
  "file_read",
  "browser_action",
  "browser_observation",
  "tool_call",
  "tool_result",
  "mcp_call",
  "mcp_result",
  "finding",
  "flag",
  "error",
  "status",
  "metric",
  "unknown",
];

describe("ReplayTimeline", () => {
  it("groups events by group_id into turn blocks", () => {
    // Message titles are display-mapped ("Agent CoT"), so the distinguishing text is the body.
    const events = [
      agentEvent({ id: "a1", group_id: "turn-1", kind: "message", body: "turn 1 msg" }),
      agentEvent({ id: "a2", group_id: "turn-1", kind: "tool_call", title: "turn 1 tool" }),
      agentEvent({ id: "b1", group_id: "turn-2", kind: "message", body: "turn 2 msg" }),
    ];
    render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    const turns = screen.getAllByTestId("replay-turn");
    expect(turns).toHaveLength(2);
    expect(turns[0]).toHaveTextContent("turn 1 msg");
    expect(turns[0]).toHaveTextContent("turn 1 tool");
    expect(turns[1]).toHaveTextContent("turn 2 msg");
  });

  it("keeps chronological order when one group_id spans the run, instead of two bunches", () => {
    // Claude Code parents every tool span under ONE interaction group_id, while log-derived
    // assistant messages carry no group_id. The old grouping vacuumed all tool spans into a single
    // turn and pushed the messages into their own bunch; contiguous grouping must preserve the
    // interleaving (msg → cmd → msg → cmd), not collapse it into 2 blocks.
    const events = [
      agentEvent({ id: "m1", kind: "message", body: "assistant-1" }), // no group_id
      agentEvent({ id: "c1:tool", kind: "terminal_command", body: "cmd-1", group_id: "SESSION" }),
      agentEvent({ id: "m2", kind: "message", body: "assistant-2" }), // no group_id
      agentEvent({ id: "c2:tool", kind: "terminal_command", body: "cmd-2", group_id: "SESSION" }),
    ];
    render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    const turns = screen.getAllByTestId("replay-turn");
    expect(turns).toHaveLength(4); // NOT 2 (all-messages bunch + all-commands bunch)
    expect(turns[0]).toHaveTextContent("assistant-1");
    expect(turns[1]).toHaveTextContent("cmd-1");
    expect(turns[2]).toHaveTextContent("assistant-2");
    expect(turns[3]).toHaveTextContent("cmd-2");
  });

  it("folds thinking by default; body is visible only after expanding", () => {
    const events = [agentEvent({ kind: "thinking", title: "pondering", body: "secret reasoning" })];
    render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    expect(screen.queryByText("secret reasoning")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Agent Thinking"));
    expect(screen.getByText("secret reasoning")).toBeInTheDocument();
  });

  it("merges a terminal command and its output into one card, output collapsed below the command", () => {
    const events = [
      agentEvent({ id: "s1:tool", kind: "terminal_command", title: "terminal", body: "ls -la", group_id: "g1" }),
      agentEvent({ id: "s1:out", kind: "terminal_output", title: "output", body: "total 48\nfile.txt", group_id: "g1" }),
    ];
    const { container } = render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    // The output is NOT a separate sibling card — it's absorbed into the command card.
    expect(container.querySelector('[data-kind="terminal_output"]')).toBeNull();
    const card = container.querySelector('[data-kind="terminal_command"]') as HTMLElement;
    expect(card).not.toBeNull();
    // Command shown; output collapsed inside the SAME card. (The command is syntax-highlighted, so
    // "ls -la" is split across coloured spans — assert on the card's text, not a single node.)
    expect(card.textContent).toContain("ls -la");
    expect(within(card).queryByText(/total 48/)).not.toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: /output/i }));
    expect(within(card).getByText(/total 48/)).toBeInTheDocument();
  });

  it("keeps the command above its output even when the output event sorts first", () => {
    // Claude Code emits `:out` < `:tool`, so the output can arrive before its command; the merged
    // card must still read command-then-output, not output-then-command.
    const events = [
      agentEvent({ id: "s2:out", kind: "terminal_output", title: "output", body: "OUT-BODY", group_id: "g2" }),
      agentEvent({ id: "s2:tool", kind: "terminal_command", title: "terminal", body: "CMD-BODY", group_id: "g2" }),
    ];
    const { container } = render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    expect(container.querySelector('[data-kind="terminal_output"]')).toBeNull();
    const card = container.querySelector('[data-kind="terminal_command"]') as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: /output/i }));
    expect(card.innerHTML.indexOf("CMD-BODY")).toBeLessThan(card.innerHTML.indexOf("OUT-BODY"));
  });

  it("merges command + output even when group_id is absent (headless traces)", () => {
    // Real Claude Code headless runs emit tool spans with NO claude_code.interaction ancestor,
    // so group_id is null; the pair must still merge, matched by their shared id base.
    const events = [
      agentEvent({ id: "CdD=:out", kind: "terminal_output", title: "output", body: "OUT-XYZ" }),
      agentEvent({ id: "CdD=:tool", kind: "terminal_command", title: "terminal", body: "CMD-XYZ" }),
    ];
    const { container } = render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    expect(container.querySelector('[data-kind="terminal_output"]')).toBeNull();
    const cards = container.querySelectorAll('[data-kind="terminal_command"]');
    expect(cards).toHaveLength(1);
    const card = cards[0] as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: /output/i }));
    expect(within(card).getByText("OUT-XYZ")).toBeInTheDocument();
  });

  it("folds an orphan terminal output (no matching command) on its own", () => {
    const events = [
      agentEvent({ kind: "terminal_output", title: "$ ls -la", body: "total 48\ndrwxr-xr-x root" }),
    ];
    render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    expect(screen.getByText("Terminal Output")).toBeInTheDocument();
    expect(screen.queryByText(/total 48/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Terminal Output"));
    expect(screen.getByText(/total 48/)).toBeInTheDocument();
  });

  it("hides metric/unknown unless the Debug toggle is on", () => {
    const events = [
      agentEvent({ kind: "metric", title: "cpu-metric" }),
      agentEvent({ kind: "unknown", title: "mystery-span" }),
      agentEvent({ kind: "message", body: "visible-message" }),
    ];
    render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    expect(screen.getByText("visible-message")).toBeInTheDocument();
    expect(screen.queryByText("cpu-metric")).not.toBeInTheDocument();
    expect(screen.queryByText("mystery-span")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("switch"));
    expect(screen.getByText("cpu-metric")).toBeInTheDocument();
    expect(screen.getByText("mystery-span")).toBeInTheDocument();
  });

  it("renders a flag event as a Flag Claim with the agent-claimed label", () => {
    const events = [agentEvent({ kind: "flag", title: "possible flag", body: "FLAG{x}" })];
    render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    expect(screen.getByText("Flag Claim")).toBeInTheDocument();
    expect(screen.getByText(/agent-claimed/i)).toBeInTheDocument();
  });

  it("keeps the trace pinned to the newest event while scrolled to the bottom", () => {
    const first = [agentEvent({ title: "e1" })];
    const { rerender } = render(<ReplayTimeline runId="r1" events={first} meta={null} />);
    const scroller = screen.getByTestId("trace-scroll");
    Object.defineProperty(scroller, "scrollHeight", { value: 500, configurable: true });
    rerender(
      <ReplayTimeline runId="r1" events={[...first, agentEvent({ title: "e2" })]} meta={null} />,
    );
    expect(scroller.scrollTop).toBe(500); // auto-followed the new event
  });

  it("offers a jump-to-latest button once scrolled up, returning to the bottom on click", () => {
    const events = [agentEvent({ title: "e1" }), agentEvent({ title: "e2" })];
    render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    const scroller = screen.getByTestId("trace-scroll");
    // Pinned at the bottom on first paint → no jump affordance.
    expect(screen.queryByRole("button", { name: /jump to latest/i })).not.toBeInTheDocument();
    // Simulate the operator scrolling up through a tall trace.
    Object.defineProperty(scroller, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(scroller, "clientHeight", { value: 300, configurable: true });
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);
    const jump = screen.getByRole("button", { name: /jump to latest/i });
    fireEvent.click(jump);
    expect(scroller.scrollTop).toBe(1000);
    expect(screen.queryByRole("button", { name: /jump to latest/i })).not.toBeInTheDocument();
  });

  it("return-to-live: clicking the pill clears the selection and re-pins to the tail", () => {
    // Selecting a span to inspect it drops the trace out of follow-tail (and time-travels the
    // terrain). The recovery pill must do BOTH halves of "return to live": clear the selection
    // (which un-freezes the terrain, owned by RunLive) and re-pin the scroll to the newest event —
    // so the operator never has to refresh to rejoin the live feed.
    const onSelectEvent = vi.fn();
    const events = [agentEvent({ id: "e1", kind: "tool_call", title: "select-me" })];
    render(
      <ReplayTimeline
        runId="r1"
        events={events}
        meta={null}
        selectedEventId="e1"
        onSelectEvent={onSelectEvent}
        attributedActionIds={new Set(["e1"])}
      />,
    );
    const scroller = screen.getByTestId("trace-scroll");
    Object.defineProperty(scroller, "scrollHeight", { value: 1200, configurable: true });
    // A selection is active → the pill reads "Return to live", not "Jump to latest".
    const live = screen.getByRole("button", { name: /return to live/i });
    fireEvent.click(live);
    expect(onSelectEvent).toHaveBeenCalledWith(null);
    expect(scroller.scrollTop).toBe(1200);
  });
});

describe("ReplayTimeline follow-tail on content growth", () => {
  beforeEach(() => {
    MockResizeObserver.instances = [];
    (globalThis as unknown as { ResizeObserver: typeof MockResizeObserver }).ResizeObserver =
      MockResizeObserver;
  });
  afterEach(() => {
    delete (globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver;
  });

  it("re-pins to the tail when existing content grows without a new entry, while at the bottom", () => {
    // The failing case the entry-count effect misses: a same-group Claude burst or a merged
    // terminal_output grows ONE existing card (entries.length unchanged). Only observing the
    // content's actual size catches it.
    const events = [agentEvent({ id: "e1", kind: "message", body: "only" })];
    render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    const scroller = screen.getByTestId("trace-scroll");
    expect(MockResizeObserver.instances.length).toBeGreaterThan(0); // observing the scroll content
    Object.defineProperty(scroller, "scrollHeight", { value: 900, configurable: true });
    MockResizeObserver.instances.forEach((ro) => ro.fire());
    expect(scroller.scrollTop).toBe(900); // followed the growth
  });

  it("does not chase the tail on content growth once the operator has scrolled up", () => {
    const events = [agentEvent({ id: "e1", kind: "message", body: "only" })];
    render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    const scroller = screen.getByTestId("trace-scroll");
    Object.defineProperty(scroller, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(scroller, "clientHeight", { value: 300, configurable: true });
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller); // reading history → atBottom flips false
    MockResizeObserver.instances.forEach((ro) => ro.fire());
    expect(scroller.scrollTop).toBe(0); // stayed where the operator left it
  });

  it("renders EventCards for every kind without crashing, in the display vocabulary", () => {
    server.use(
      http.get("*/api/runs/r1/events/*", () =>
        HttpResponse.json({ event_id: "x", raw_ref: null, spans: [] }),
      ),
    );
    const events = ALL_KINDS.map((kind) => agentEvent({ kind, title: `k-${kind}` }));
    expect(() =>
      render(<ReplayTimeline runId="r1" events={events} meta={null} />),
    ).not.toThrow();
    // Toggle Debug so metric/unknown (otherwise hidden) are present too.
    fireEvent.click(screen.getByRole("switch"));
    // Mapped display labels (raw titles stay as tooltips)…
    expect(screen.getByText("Agent CoT")).toBeInTheDocument();
    expect(screen.getByText("Agent Thinking")).toBeInTheDocument();
    expect(screen.getByText("Agent Terminal")).toBeInTheDocument();
    expect(screen.getByText("Terminal Output")).toBeInTheDocument();
    expect(screen.getByText("Agent File Edit")).toBeInTheDocument();
    expect(screen.getByText("Agent File Read")).toBeInTheDocument();
    expect(screen.getAllByText("Agent Browser")).toHaveLength(2);
    expect(screen.getByText("Agent k-tool_call")).toBeInTheDocument(); // tool name prefixed
    expect(screen.getByText("Tool Result")).toBeInTheDocument();
    expect(screen.getAllByText("Agent MCP")).toHaveLength(2);
    expect(screen.getByText("Agent Finding")).toBeInTheDocument();
    expect(screen.getByText("Flag Claim")).toBeInTheDocument();
    // …while error/status/metric/unknown keep their raw titles.
    for (const kind of ["error", "status", "metric", "unknown"] as const) {
      expect(screen.getByText(`k-${kind}`)).toBeInTheDocument();
    }
  });

  it("scrolls the newly selected event's card into view and suppresses follow-tail", () => {
    // TimelineWaterfall bar clicks land here as a selectedEventId change — the trace must bring
    // the card on screen and stop chasing the tail so the re-pin doesn't yank back down.
    const scrollIntoView = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = scrollIntoView;
    const events = [
      agentEvent({ id: "e1", kind: "message", body: "first" }),
      agentEvent({ id: "e2", kind: "message", body: "second" }),
    ];
    const { rerender } = render(
      <ReplayTimeline runId="r1" events={events} meta={null} selectedEventId={null} />,
    );
    expect(scrollIntoView).not.toHaveBeenCalled();
    rerender(<ReplayTimeline runId="r1" events={events} meta={null} selectedEventId="e2" />);
    expect(scrollIntoView).toHaveBeenCalled();
    // Follow-tail suppressed → the return-to-live affordance surfaces. While a span is selected the
    // pill reads "Return to live" (it also exits terrain time-travel), not the bare "Jump to latest".
    expect(screen.getByRole("button", { name: /return to live/i })).toBeInTheDocument();
  });

  it("shows the event time on cards and rows", () => {
    const events = [
      agentEvent({ kind: "message", ts: "2026-07-03T10:00:00Z" }),
      agentEvent({ kind: "thinking", ts: "2026-07-03T10:00:01Z" }),
    ];
    render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    const times = screen.getAllByTestId("event-time");
    expect(times).toHaveLength(2); // the EventCard and the FoldableRow both carry a stamp
    for (const t of times) expect(t.textContent).toMatch(/\d{2}:\d{2}:\d{2}/);
  });

  it("click-to-replay: clicking an event row selects it without swallowing the raw-drilldown button", () => {
    // Binds the Trace timeline to the Terrain map (Slice 3b): clicking a row must fire
    // onSelectEvent with that event's id and mark the row `data-selected`; the pre-existing
    // "View raw" button must keep working and must NOT also trigger a selection change.
    const onSelectEvent = vi.fn();
    // Only spans that produced a terrain action are selectable (time-travel), so use attributed
    // action spans here — a plain message is intentionally inert (see the dedicated test below).
    const events = [
      agentEvent({ id: "e1", kind: "tool_call", title: "select-me" }),
      agentEvent({ id: "e2", kind: "tool_call", title: "other-event" }),
    ];
    const attributedActionIds = new Set(["e1", "e2"]);
    const { container } = render(
      <ReplayTimeline
        runId="r1"
        events={events}
        meta={null}
        selectedEventId={null}
        onSelectEvent={onSelectEvent}
        attributedActionIds={attributedActionIds}
      />,
    );

    const card = container.querySelector('[data-kind="tool_call"]') as HTMLElement;
    expect(card.getAttribute("data-selected")).toBeNull();

    fireEvent.click(screen.getByText("Agent select-me"));
    expect(onSelectEvent).toHaveBeenCalledWith("e1");
    expect(onSelectEvent).toHaveBeenCalledTimes(1);

    // Re-render with the selection applied (as RunLive would, feeding selectedEventId back in) —
    // the row now carries the selected marker.
    const { container: container2 } = render(
      <ReplayTimeline
        runId="r1"
        events={events}
        meta={null}
        selectedEventId="e1"
        onSelectEvent={onSelectEvent}
        attributedActionIds={attributedActionIds}
      />,
    );
    const selectedCard = container2.querySelector('[data-kind="tool_call"]') as HTMLElement;
    expect(selectedCard.getAttribute("data-selected")).toBe("true");

    // Clicking the raw-drilldown button must NOT also change the selection (stopPropagation).
    onSelectEvent.mockClear();
    fireEvent.click(within(selectedCard).getByRole("button", { name: /view raw/i }));
    expect(onSelectEvent).not.toHaveBeenCalled();
    // ...and the raw modal still opens (the existing onViewRaw seam still fires).
    expect(screen.getByRole("dialog", { name: /select-me/i })).toBeInTheDocument();
  });

  it("keyboard access: a selectable event row is focusable and Enter/Space select it", () => {
    // FIX 2 (Slice 3b review): the row-select onClick lived on a bare div with no keyboard
    // support. A selectable row must be focusable and fire the same select on Enter or Space.
    const onSelectEvent = vi.fn();
    const events = [agentEvent({ id: "e1", kind: "tool_call", title: "select-me" })];
    const { container } = render(
      <ReplayTimeline runId="r1" events={events} meta={null} onSelectEvent={onSelectEvent}
        attributedActionIds={new Set(["e1"])} />,
    );
    const card = container.querySelector('[data-kind="tool_call"]') as HTMLElement;
    expect(card.tabIndex).toBe(0);
    expect(card.getAttribute("role")).toBe("button");

    fireEvent.keyDown(card, { key: "Enter" });
    expect(onSelectEvent).toHaveBeenCalledWith("e1");

    onSelectEvent.mockClear();
    fireEvent.keyDown(card, { key: " " });
    expect(onSelectEvent).toHaveBeenCalledWith("e1");
  });

  it("non-selectable event rows are not keyboard-focusable", () => {
    // Only apply role/tabIndex/onKeyDown when the row is actually selectable — a row with no
    // onSelectEvent wired up must not become focusable.
    const events = [agentEvent({ id: "e1", kind: "message", title: "plain-row" })];
    const { container } = render(<ReplayTimeline runId="r1" events={events} meta={null} />);
    const card = container.querySelector('[data-kind="message"]') as HTMLElement;
    expect(card.getAttribute("tabIndex")).toBeNull();
    expect(card.getAttribute("role")).toBeNull();
  });

  it("an UN-attributed span is inert: not focusable and a click does not drive time-travel", () => {
    // Time-travel is gated to spans that produced a terrain action. A span with no update must not
    // be selectable — otherwise selecting it falls back to the latest fold and yanks the map to the
    // fully-completed (green) state (the reported bug). Here e1 is NOT in attributedActionIds.
    const onSelectEvent = vi.fn();
    const events = [agentEvent({ id: "e1", kind: "tool_call", title: "recon" })];
    const { container } = render(
      <ReplayTimeline runId="r1" events={events} meta={null} onSelectEvent={onSelectEvent}
        attributedActionIds={new Set()} />,
    );
    const card = container.querySelector('[data-kind="tool_call"]') as HTMLElement;
    expect(card.getAttribute("role")).toBeNull(); // not selectable
    expect(card.getAttribute("tabIndex")).toBeNull();
    fireEvent.click(screen.getByText("Agent recon"));
    expect(onSelectEvent).not.toHaveBeenCalled(); // clicking is inert — no yank to latest
  });

  it("a terminal card is 'action' when its OUTPUT sub-event is attributed, not only its command", () => {
    // The attributor attaches the terrain action to whichever sub-event carries it — for a terminal
    // span that is almost always the OUTPUT (where the result/flag lands). The merged card, keyed on
    // the command, must still light green: status = action if ANY merged sub-event is attributed.
    const events = [
      agentEvent({ id: "sp=:tool", kind: "terminal_command", title: "cmd", body: "curl internal", group_id: "g" }),
      agentEvent({ id: "sp=:out", kind: "terminal_output", title: "out", body: "XORCISE{flag}", group_id: "g" }),
    ];
    const { container } = render(
      <ReplayTimeline runId="r1" events={events} meta={null} attributedActionIds={new Set(["sp=:out"])} />,
    );
    const card = container.querySelector('[data-kind="terminal_command"]') as HTMLElement;
    const dot = card.querySelector('[data-testid="attr-status"]') as HTMLElement;
    expect(dot.getAttribute("data-status")).toBe("action");
  });

  it("a terminal card is 'analyzed' when a sub-event was considered but none attributed", () => {
    const events = [
      agentEvent({ id: "q=:tool", kind: "terminal_command", title: "cmd", body: "ls", group_id: "g" }),
      agentEvent({ id: "q=:out", kind: "terminal_output", title: "out", body: "nothing", group_id: "g" }),
    ];
    const { container } = render(
      <ReplayTimeline runId="r1" events={events} meta={null} consideredIds={new Set(["q=:out"])} />,
    );
    const card = container.querySelector('[data-kind="terminal_command"]') as HTMLElement;
    const dot = card.querySelector('[data-testid="attr-status"]') as HTMLElement;
    expect(dot.getAttribute("data-status")).toBe("analyzed");
  });

  it("reverse hover link: a peer-hovered event gets the light-highlight marker, distinct from selected", () => {
    // Terrain → Trace (Slice 3b/T4): hovering a map node reports the event ids of the actions
    // it targeted; RunLive feeds those in as `hoveredEventIds` for a lighter emphasis than the
    // bold click-selection.
    const events = [
      agentEvent({ id: "e1", kind: "message", title: "peer-hovered" }),
      agentEvent({ id: "e2", kind: "message", title: "untouched" }),
    ];
    const { container } = render(
      <ReplayTimeline
        runId="r1"
        events={events}
        meta={null}
        selectedEventId={null}
        hoveredEventIds={["e1"]}
      />,
    );
    const cards = container.querySelectorAll('[data-kind="message"]');
    expect(cards[0].getAttribute("data-peer-hover")).toBe("true");
    expect(cards[1].getAttribute("data-peer-hover")).toBeNull();

    // Peer-hover defers to the bold selected style — a simultaneously-selected event does NOT
    // also carry the lighter peer-hover marker.
    const { container: container2 } = render(
      <ReplayTimeline
        runId="r1"
        events={events}
        meta={null}
        selectedEventId="e1"
        hoveredEventIds={["e1"]}
      />,
    );
    const selectedAndHovered = container2.querySelectorAll('[data-kind="message"]')[0];
    expect(selectedAndHovered.getAttribute("data-selected")).toBe("true");
    expect(selectedAndHovered.getAttribute("data-peer-hover")).toBeNull();
  });
});

describe("ReplayTimeline attribution status", () => {
  const cmd = () =>
    agentEvent({ id: "c1:tool", kind: "terminal_command", title: "curl", group_id: "g" });

  it("marks a span whose action produced an edge as completed", () => {
    const { container } = render(
      <ReplayTimeline
        runId="r1"
        events={[cmd()]}
        attributedActionIds={new Set(["c1:tool"])}
        consideredIds={new Set(["c1:tool"])}
      />,
    );
    const dot = container.querySelector('[data-testid="attr-status"]');
    expect(dot?.getAttribute("data-status")).toBe("action");
  });

  it("marks a considered-but-inapplicable span as analyzed", () => {
    const { container } = render(
      <ReplayTimeline
        runId="r1"
        events={[cmd()]}
        attributedActionIds={new Set()}
        consideredIds={new Set(["c1:tool"])}
      />,
    );
    expect(
      container.querySelector('[data-testid="attr-status"]')?.getAttribute("data-status"),
    ).toBe("analyzed");
  });

  it("marks an unattributed span in-progress while a batch is running, else pending", () => {
    const running = render(
      <ReplayTimeline runId="r1" events={[cmd()]} attributing={true} />,
    );
    expect(
      running.container.querySelector('[data-testid="attr-status"]')?.getAttribute("data-status"),
    ).toBe("in-progress");

    const idle = render(<ReplayTimeline runId="r1" events={[cmd()]} attributing={false} />);
    const dot = idle.container.querySelector('[data-testid="attr-status"]');
    expect(dot?.getAttribute("data-status")).toBe("pending");
    expect(dot?.className).toContain("bg-err"); // a non-attributed span is marked red
  });

  it("shows no status dot for a non-attributable (conversation) span", () => {
    const { container } = render(
      <ReplayTimeline runId="r1" events={[agentEvent({ id: "m1", kind: "message" })]} />,
    );
    expect(container.querySelector('[data-testid="attr-status"]')).toBeNull();
  });
});

describe("ReplayTimeline infra rows", () => {
  const infra = (over: Partial<import("@/features/live-trace/terrain-fold").InfraRow> = {}) => ({
    id: "infra:3",
    ts: new Date(2500).toISOString(),
    label: "Agent joined the tailnet",
    targetId: "hs:join",
    seq: 3,
    ...over,
  });

  it("renders an infra activity as a clickable row that selects its synthetic id", () => {
    const onSelectEvent = vi.fn();
    render(
      <ReplayTimeline
        runId="r1"
        events={[agentEvent({ id: "m1", kind: "message", title: "hello" })]}
        infraRows={[infra()]}
        onSelectEvent={onSelectEvent}
      />,
    );
    const row = screen.getByTestId("infra-row");
    expect(row).toHaveTextContent("Agent joined the tailnet");
    fireEvent.click(row);
    expect(onSelectEvent).toHaveBeenCalledWith("infra:3");
  });

  it("interleaves an infra row between agent turns by receipt-time", () => {
    // Agent producer clocks disagree, but receipt times at 1000ms and 3000ms bracket infra at 2000.
    // Message titles are display-mapped ("Agent CoT"), so the distinguishing text rides in body.
    const first = agentEvent({ id: "first", kind: "message", body: "first-msg" });
    const second = agentEvent({ id: "second", kind: "message", body: "second-msg" });
    render(
      <ReplayTimeline
        runId="r1"
        events={[
          {
            ...first,
            ts: new Date(9000).toISOString(),
            received_at: new Date(1000).toISOString(),
          },
          {
            ...second,
            ts: new Date(500).toISOString(),
            received_at: new Date(3000).toISOString(),
          },
        ]}
        infraRows={[infra({ id: "infra:1", ts: new Date(2000).toISOString(), label: "Fetched the run brief" })]}
      />,
    );
    const rows = screen.getAllByTestId(/replay-turn|infra-turn/);
    const text = rows.map((r) => r.textContent);
    // order: first-msg, [infra] Fetched the run brief, second-msg
    expect(text[0]).toContain("first-msg");
    expect(text[1]).toContain("Fetched the run brief");
    expect(text[2]).toContain("second-msg");
  });

  it("lets infra split a long same-group Claude segment", () => {
    const events = [
      agentEvent({
        id: "cmd-1",
        group_id: "WHOLE-SESSION",
        body: "before-infra",
        received_at: new Date(1000).toISOString(),
      }),
      agentEvent({
        id: "cmd-2",
        group_id: "WHOLE-SESSION",
        body: "after-infra",
        received_at: new Date(3000).toISOString(),
      }),
    ];

    render(
      <ReplayTimeline
        runId="r1"
        events={events}
        infraRows={[
          infra({
            ts: new Date(2000).toISOString(),
            label: "Agent fetched mission attachment",
          }),
        ]}
      />,
    );

    const rows = screen.getAllByTestId(/replay-turn|infra-turn/);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("before-infra");
    expect(rows[1]).toHaveTextContent("Agent fetched mission attachment");
    expect(rows[2]).toHaveTextContent("after-infra");
  });

  it("places a delayed older producer event by receipt time", () => {
    render(
      <ReplayTimeline
        runId="r1"
        events={[
          agentEvent({
            id: "late",
            body: "late-export",
            ts: new Date(1000).toISOString(),
            received_at: new Date(3000).toISOString(),
          }),
        ]}
        infraRows={[
          infra({
            ts: new Date(2000).toISOString(),
            label: "Infra already observed",
          }),
        ]}
      />,
    );

    const rows = screen.getAllByTestId(/replay-turn|infra-turn/);
    expect(rows[0]).toHaveTextContent("Infra already observed");
    expect(rows[1]).toHaveTextContent("late-export");
  });

  it("orders infra before an agent card on an exact receipt-time tie", () => {
    const tied = new Date(2000).toISOString();
    render(
      <ReplayTimeline
        runId="r1"
        events={[agentEvent({ body: "agent-action", received_at: tied })]}
        infraRows={[infra({ ts: tied, label: "Infra prerequisite" })]}
      />,
    );

    const rows = screen.getAllByTestId(/replay-turn|infra-turn/);
    expect(rows[0]).toHaveTextContent("Infra prerequisite");
    expect(rows[1]).toHaveTextContent("agent-action");
  });

  it("marks the selected infra row", () => {
    render(
      <ReplayTimeline
        runId="r1"
        events={[agentEvent({ id: "m1", kind: "message" })]}
        infraRows={[infra({ id: "infra:5" })]}
        selectedEventId="infra:5"
      />,
    );
    expect(screen.getByTestId("infra-row").getAttribute("data-selected")).toBe("true");
  });
});
