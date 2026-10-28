import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { runFixture } from "@/test/fixtures";
import { BackendStatusBulb, connectionChips } from "./run-status";
import { runPhase } from "./run-phase";

describe("BackendStatusBulb", () => {
  it("shows the phase label + help text", () => {
    const phase = runPhase(runFixture({ state: "created" }), 0);
    render(<BackendStatusBulb phase={phase} />);
    expect(screen.getByText("Awaiting agent")).toBeInTheDocument();
    expect(screen.getByText(/OpenTelemetry/i)).toBeInTheDocument();
  });
});

describe("connectionChips", () => {
  const labels = (env?: Parameters<typeof connectionChips>[0]) =>
    connectionChips(env).map((c) => `${c.label}: ${c.value}`);

  it("an environment not observed yet reads as still starting", () => {
    expect(labels()).toEqual(["Environment: Starting"]);
  });

  it("never labels a chip 'Agent' — that word belongs to the harness identity alone", () => {
    // The run header carries `Harness: codex`; a chip called "Agent" holding a LINK state made
    // one word mean two things in the same strip. The link state lives in the status bulb.
    const chips = labels({ run_id: "r1", state: "ready", ready: true, detail: "", release_attempts: 0 });
    expect(chips).toEqual(["Environment: Ready"]);
  });

  it("a finished run's environment reads as released, never live-green", () => {
    // The server reports "released" for a terminal run once its teardown has landed (and
    // "releasing" until then, #173), so the chip can never show a present-tense "Ready".
    const chips = labels({
      run_id: "r1",
      state: "released",
      ready: false,
      detail: "",
      release_attempts: 0,
    });
    expect(chips).toEqual(["Environment: Released"]);
  });

  it("has no Objective chip, which only restated the badge and overpromised (#38)", () => {
    // It read "Solved" exactly when the run completed: a run that completed and failed its
    // objective read "Solved". The grade, on the result page, is what says that.
    const chips = connectionChips({
      run_id: "r1",
      state: "released",
      ready: false,
      detail: "",
      release_attempts: 0,
    });
    expect(chips.map((c) => c.label)).not.toContain("Objective");
  });
});
