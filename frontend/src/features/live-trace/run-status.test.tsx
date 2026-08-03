import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { runFixture } from "@/test/fixtures";
import { BackendStatusBulb, ConnectionPanel } from "./run-status";
import { runPhase } from "./run-phase";

describe("BackendStatusBulb", () => {
  it("shows the phase label + help text", () => {
    const phase = runPhase({ state: "created", terminal_trigger: null }, 0);
    render(<BackendStatusBulb phase={phase} />);
    expect(screen.getByText("Awaiting agent")).toBeInTheDocument();
    expect(screen.getByText(/OpenTelemetry/i)).toBeInTheDocument();
  });
});

describe("ConnectionPanel", () => {
  it("created + no trace → env still provisioning, no objective cell yet", () => {
    render(
      <ConnectionPanel
        run={runFixture({ state: "created", sandbox_ref: null })}
        eventCount={0}
      />,
    );
    expect(screen.getByText("Environment")).toBeInTheDocument();
    expect(screen.getByText("Starting")).toBeInTheDocument(); // env still provisioning
    // "In Progress" restated the run badge; gating Objective to terminal keeps the header
    // strip short enough that no cell orphans onto a half-empty row.
    expect(screen.queryByText("Objective")).toBeNull();
    expect(screen.queryByText("In Progress")).toBeNull();
  });

  it("never labels a chip 'Agent' — that word belongs to the harness identity alone", () => {
    // The run header carries `Harness: codex`; a chip called "Agent" holding a LINK state made
    // one word mean two things in the same strip. The link state lives in the status bulb.
    render(
      <ConnectionPanel
        run={runFixture({ state: "active", sandbox_ref: "xorcise/chal:local" })}
        eventCount={4}
        // The env state is now OBSERVED by the server rather than guessed from sandbox_ref (which
        // could not tell a static mission from a dead environment).
        environment={{ run_id: "r1", state: "ready", ready: true, detail: "" }}
      />,
    );
    expect(screen.queryByText("Agent")).toBeNull();
    expect(screen.queryByText("Connected")).toBeNull();
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("terminal done → objective solved", () => {
    render(
      <ConnectionPanel
        run={runFixture({ state: "terminal", terminal_trigger: "done" })}
        eventCount={9}
      />,
    );
    expect(screen.getByText("Objective")).toBeInTheDocument();
    expect(screen.getByText("Solved")).toBeInTheDocument();
  });

  it("terminal run → env reads as released, not live-green", () => {
    // A terminated run's env has been torn down — a present-tense green "Ready" would
    // misrepresent it as still live.
    render(
      <ConnectionPanel
        run={runFixture({
          state: "terminal",
          terminal_trigger: "operator",
          sandbox_ref: "xorcise/chal:local",
        })}
        eventCount={5}
        // The server reports "released" for any terminal run (asserted server-side too), so the
        // chip can never show a present-tense green "Ready" for a torn-down environment.
        environment={{ run_id: "r1", state: "released", ready: false, detail: "" }}
      />,
    );
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.getByText("Released")).toBeInTheDocument();
    expect(screen.getByText("Not Solved")).toBeInTheDocument();
  });
});
