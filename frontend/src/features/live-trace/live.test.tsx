import { describe, it, expect, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture } from "@/test/fixtures";
import type { AgentEvent, RunEventsView } from "@/lib/api/types";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { RunLive } from "./run-live";

// The trace section now renders the server-normalized AgentEvent model
// (GET /runs/{id}/events) rather than raw OTLP records — mocks return
// AgentEvent shapes directly instead of OTLP span payloads.
function agentEvent(overrides: Partial<AgentEvent> = {}): AgentEvent {
  const runId = overrides.run_id ?? "r1";
  return {
    run_id: runId,
    id: overrides.id ?? "s1",
    ts: overrides.ts ?? new Date(1_000).toISOString(),
    // A fresh server-side receipt by default — the fixture `ts` is epoch-1970, which would
    // (correctly) read as an agent stalled for decades. Stall tests override this.
    received_at: new Date().toISOString(),
    source_agent: overrides.source_agent ?? "generic",
    kind: overrides.kind ?? "terminal_command",
    role: overrides.role ?? "agent",
    title: overrides.title ?? "exec_shell",
    body: overrides.body ?? "whoami",
    data: overrides.data ?? {},
    severity: overrides.severity ?? "info",
    raw_ref: overrides.raw_ref ?? {
      run_id: runId,
      raw_seq: 1,
      span_id: "s1",
      signal: "trace",
    },
    ...overrides,
  };
}

function eventsView(runId: string, events: AgentEvent[]): RunEventsView {
  return {
    run_id: runId,
    source_agent: "generic",
    adapter_name: "generic",
    adapter_version: "1",
    fallback: false,
    next_since: events.length ? Math.max(...events.map((e) => e.raw_ref.raw_seq)) : -1,
    counts: {},
    warnings: [],
    events,
  };
}

describe("RunLive", () => {
  it("renders a not-found state for a missing run", async () => {
    server.use(http.get("*/api/runs", () => HttpResponse.json([])));
    renderWithProviders(<RunLive runId="ghost" />);
    await waitFor(() =>
      expect(screen.getByText("Run not found")).toBeInTheDocument(),
    );
  });

  it("renders the run header and live replay events", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "r1", mission: "sqli-login", name: "recon-run", state: "active" }),
        ]),
      ),
      http.get("*/api/runs/r1/events", () =>
        HttpResponse.json(eventsView("r1", [agentEvent({ title: "exec_shell" })])),
      ),
    );
    renderWithProviders(<RunLive runId="r1" />);
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "recon-run" }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("Running")).toBeInTheDocument();
    // terminal_command renders under its display-layer label (raw title stays a tooltip).
    await waitFor(() =>
      expect(screen.getByText("Agent Terminal")).toBeInTheDocument(),
    );
  });

  it("does not fetch or render harness capability support", async () => {
    let capabilityRequests = 0;
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "r-cap", state: "active" }),
        ]),
      ),
      http.get("*/api/runs/r-cap/events", () =>
        HttpResponse.json(eventsView("r-cap", [agentEvent({ run_id: "r-cap" })])),
      ),
      http.get("*/api/harnesses/capabilities", () => {
        capabilityRequests += 1;
        return HttpResponse.json([]);
      }),
    );

    renderWithProviders(<RunLive runId="r-cap" />);
    await screen.findByText("Agent Terminal");

    expect(capabilityRequests).toBe(0);
    expect(screen.queryByText(/telemetry profile/i)).not.toBeInTheDocument();
  });

  it("shows the prompt hand-off while created with no trace, then the bulb", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "rp", mission: "sqli-login", state: "created" }),
        ]),
      ),
      http.get("*/api/runs/rp/events", () =>
        HttpResponse.json(eventsView("rp", [])),
      ),
      http.get("*/api/runs/rp/prompt", () =>
        HttpResponse.json({ run_id: "rp", prompt: "PASTE-ME-INTO-AGENT" }),
      ),
    );
    renderWithProviders(<RunLive runId="rp" />);
    // Backend-status bulb in the awaiting state.
    await waitFor(() =>
      expect(screen.getByText("Awaiting agent")).toBeInTheDocument(),
    );
    // The prompt hand-off card is visible (agent hasn't started).
    await waitFor(() =>
      expect(screen.getByText("PASTE-ME-INTO-AGENT")).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: /copy prompt/i }),
    ).toBeInTheDocument();
  });

  it("hides the prompt hand-off once the trace is streaming", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "rs", mission: "sqli-login", state: "active" }),
        ]),
      ),
      http.get("*/api/runs/rs/events", () =>
        HttpResponse.json(eventsView("rs", [agentEvent({ run_id: "rs" })])),
      ),
    );
    renderWithProviders(<RunLive runId="rs" />);
    await waitFor(() =>
      expect(screen.getByText("Streaming")).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: /copy prompt/i }),
    ).not.toBeInTheDocument();
  });

  it("auto-updates to the terminal state without a manual refresh", async () => {
    let gets = 0;
    server.use(
      http.get("*/api/runs", () => {
        gets += 1;
        // First load is active; the run then finishes (timeout) — the view must
        // pick this up by polling, with no manual refresh.
        const done = gets >= 2;
        return HttpResponse.json([
          runFixture({
            run_id: "r3",
            mission: "sqli-login",
            state: done ? "terminal" : "active",
            terminal_trigger: done ? "timeout" : null,
          }),
        ]);
      }),
      http.get("*/api/runs/r3/events", () =>
        HttpResponse.json(eventsView("r3", [])),
      ),
    );
    renderWithProviders(<RunLive runId="r3" />);
    await screen.findByText("Running");
    // The run finishes on a timeout → the dedicated timeout message surfaces its
    // "View partial result" action and the "Running" badge is gone.
    await waitFor(
      () => expect(screen.getByText(/View partial result/i)).toBeInTheDocument(),
      { timeout: 5000 },
    );
    expect(screen.queryByText("Running")).toBeNull();
  });

  it("links the mission fact to its detail page", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "r1", mission: "sqli-login", name: "recon-run", state: "active" }),
        ]),
      ),
      http.get("*/api/runs/r1/events", () =>
        HttpResponse.json(eventsView("r1", [])),
      ),
    );
    renderWithProviders(<RunLive runId="r1" />);
    // The run's title is now its name; the mission reads as a fact (name + version) that still
    // links to the mission's detail page.
    const link = await screen.findByRole("link", { name: "sqli-login v1" });
    expect(link).toHaveAttribute("href", "/missions/detail?id=sqli-login");
  });

  it("renders the Terrain + Trace sections and shows the attribution-off notice when no terrain model is configured", async () => {
    // Fix 3 (Slice 3b final review): pins the untested RunLive glue — attributionOff derives
    // from useConfig().terrain.configured and is wired into TerrainMap alongside the Trace pane.
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "rt", mission: "sqli-login", state: "active" }),
        ]),
      ),
      http.get("*/api/runs/rt/events", () =>
        HttpResponse.json(eventsView("rt", [agentEvent({ run_id: "rt", title: "exec_shell" })])),
      ),
      http.get("*/api/runs/rt/terrain2", () =>
        HttpResponse.json({
          run_id: "rt",
          mission_id: "sqli-login",
          summary: null,
          groups: [{ id: "g", label: "Group", description: null, kind: "segment", order: 0, hidden: false, discovered: true }],
          nodes: [
            { id: "web", label: "web", group: "g", type: "service", objective: false, description: null,
              discovery_condition: null, completion_condition: null, state: "defined" },
          ],
          edges: [],
          updates: [],
          attribution: null,
          objective_id: null,
        }),
      ),
      http.get("*/api/config", () =>
        HttpResponse.json({
          judge: {
            configured: false,
            base_url: null,
            model_name: null,
            key_hint: null,
            timeout_seconds: 120,
            transcript_max_bytes: 262144,
          },
          terrain: {
            configured: false,
            uses_judge_default: true,
            base_url: null,
            model_name: null,
            key_hint: null,
          },
          default_budget_seconds: 600,
          catalog: { connected: true, url: "https://catalog.example.com" },
          network: { headscale_url: null, advertise_host: null },
        }),
      ),
    );
    renderWithProviders(<RunLive runId="rt" />);
    // Below the wide breakpoint (jsdom has no matchMedia → narrow) the work area is tabs;
    // Trace is active by default, Terrain is a click away.
    expect(await screen.findByRole("tab", { name: "Terrain" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "Trace" })).toBeInTheDocument();
    expect(await screen.findByText("Agent Terminal")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Terrain" }));
    expect(await screen.findByText(/attribution off/i)).toBeInTheDocument();
  });

  it("merges the connection chips into the header card with one label per fact", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "rc", mission: "sqli-login", name: "recon-run", state: "active" }),
        ]),
      ),
      http.get("*/api/runs/rc/events", () =>
        HttpResponse.json(eventsView("rc", [agentEvent({ run_id: "rc" })])),
      ),
    );
    renderWithProviders(<RunLive runId="rc" />);
    await screen.findByRole("heading", { name: "recon-run" });
    // Every label in the header strip names exactly ONE thing: Agent (the identity) and Harness
    // are now distinct cells (getByText throws if either label appears more than once), and the
    // connection state lives under its own labels.
    expect(screen.getByText("Agent")).toBeInTheDocument();
    expect(screen.getByText("Harness")).toBeInTheDocument();
    expect(screen.getByText("Environment")).toBeInTheDocument();
    expect(await screen.findByText("Starting")).toBeInTheDocument();
    // The agent link state lives in the status bulb, not a duplicate cell; and "Objective" is
    // terminal-only, so a live run has no cell that can orphan onto a half-empty row.
    expect(screen.getByText("Streaming")).toBeInTheDocument();
    expect(screen.queryByText("Objective")).toBeNull();
  });

  it("keeps the launch-command copy visible in the CREATED state (not below a code block)", async () => {
    // The reported defect: the copy control existed but rendered ~300px below the clip line of
    // the hand-off card's scroller, so operators hand-selected a block that scrolled in BOTH
    // axes. The action is now pinned to the card head, above the payload.
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "rl", mission: "ghostwire", state: "created" }),
        ]),
      ),
      http.get("*/api/runs/rl/events", () => HttpResponse.json(eventsView("rl", []))),
      http.get("*/api/runs/rl/prompt", () =>
        HttpResponse.json({ run_id: "rl", prompt: "MISSION" }),
      ),
      http.get("*/api/runs/rl/launch-profile", () =>
        HttpResponse.json({
          run_id: "rl",
          env: { OTEL_RESOURCE_ATTRIBUTES: "xorcise.run_id=rl" },
          correlation: "resource-attr",
          notes: [],
          fallback: false,
          launch_mode: "host",
          launch_modes: ["host"],
          command: "codex exec 'solve it'",
          shell_block: "export OTEL_RESOURCE_ATTRIBUTES=xorcise.run_id=rl\ncodex exec 'solve it'",
          tips: [],
        }),
      ),
    );
    renderWithProviders(<RunLive runId="rl" />);
    expect(
      await screen.findByRole("button", { name: /copy launch command/i }),
    ).toBeInTheDocument();
    // Exactly one — the header's demoted ghost copy only appears once the card has yielded.
    expect(screen.getAllByRole("button", { name: /copy launch command/i })).toHaveLength(1);
    // The Timeline strip does not bill a card for an empty waterfall in this state.
    expect(screen.queryByText("Timeline")).toBeNull();
  });

  it("demotes the launch-command copy into the header once events are streaming", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "rd", mission: "ghostwire", state: "active" }),
        ]),
      ),
      http.get("*/api/runs/rd/events", () =>
        HttpResponse.json(eventsView("rd", [agentEvent({ run_id: "rd" })])),
      ),
      http.get("*/api/runs/rd/launch-profile", () =>
        HttpResponse.json({
          run_id: "rd",
          env: {},
          correlation: "resource-attr",
          notes: [],
          fallback: false,
          launch_mode: "host",
          launch_modes: ["host"],
          command: "codex exec 'solve it'",
          shell_block: "codex exec 'solve it'",
          tips: [],
        }),
      ),
    );
    renderWithProviders(<RunLive runId="rd" />);
    await screen.findByText("Streaming");
    // The hand-off card is gone (the trace owns the pane) but the command is still one click away.
    expect(screen.queryByText(/Action required/i)).toBeNull();
    expect(
      await screen.findByRole("button", { name: /copy launch command/i }),
    ).toBeInTheDocument();
  });

  it("warns when a streaming run goes silent past the threshold; Dismiss clears the banner only", async () => {
    // The backlog's received_at is months old, so the stall clock seeds far past the default
    // 3-minute threshold and the warning is due on first render — no fake timers needed.
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "rz", mission: "sqli-login", state: "active" }),
        ]),
      ),
      http.get("*/api/runs/rz/events", () =>
        HttpResponse.json(
          eventsView("rz", [
            agentEvent({ run_id: "rz", received_at: "2026-01-01T00:00:00.000Z" }),
          ]),
        ),
      ),
    );
    renderWithProviders(<RunLive runId="rz" />);
    expect(await screen.findByText("Agent may have stopped")).toBeInTheDocument();
    // The status bulb reads Stalled, not a green "watch it work" pulse.
    expect(screen.getByText("Stalled")).toBeInTheDocument();
    expect(screen.queryByText("Streaming")).toBeNull();
    expect(screen.getByRole("button", { name: /terminate run/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^dismiss$/i }));
    expect(screen.queryByText("Agent may have stopped")).toBeNull();
    // Dismiss silences the banner; the bulb keeps reporting the stall.
    expect(screen.getByText("Stalled")).toBeInTheDocument();
  });

  it("does not warn while telemetry is fresh", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "rf", mission: "sqli-login", state: "active" }),
        ]),
      ),
      http.get("*/api/runs/rf/events", () =>
        HttpResponse.json(
          eventsView("rf", [
            agentEvent({ run_id: "rf", received_at: new Date().toISOString() }),
          ]),
        ),
      ),
    );
    renderWithProviders(<RunLive runId="rf" />);
    await screen.findByText("Streaming");
    expect(screen.queryByText("Agent may have stopped")).toBeNull();
  });

  it("shows a View result link for a terminal run", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({
            run_id: "r2",
            mission: "idor",
            state: "terminal",
            terminal_trigger: "done",
          }),
        ]),
      ),
      http.get("*/api/runs/r2/events", () =>
        HttpResponse.json(eventsView("r2", [])),
      ),
    );
    renderWithProviders(<RunLive runId="r2" />);
    await waitFor(() =>
      expect(screen.getByText(/View result/i)).toBeInTheDocument(),
    );
  });
});
