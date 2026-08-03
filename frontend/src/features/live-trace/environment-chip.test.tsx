import { describe, it, expect } from "vitest";
import { environmentChip } from "./run-status";
import type { RunEnvironment } from "@/lib/api/types";

const env = (over: Partial<RunEnvironment>): RunEnvironment => ({
  run_id: "r1",
  state: "starting",
  ready: false,
  detail: "",
  ...over,
});

describe("environmentChip", () => {
  it("reports a static mission as having no environment, not a perpetual Starting", () => {
    // The reported symptom: a static (attachment-only) mission has no environment BY DESIGN, so
    // "Starting" told the operator to wait for something that was never coming.
    const chip = environmentChip(env({ state: "none" }));
    expect(chip.value).toBe("None");
    expect(chip.tone).toBe("muted");
  });

  it("reports a live environment as Ready", () => {
    const chip = environmentChip(env({ state: "ready", ready: true }));
    expect(chip.value).toBe("Ready");
    expect(chip.tone).toBe("green");
  });

  it("reports a dead environment as Failed in red", () => {
    // Previously an environment that had DIED still read as "Ready" — no signal at all.
    const chip = environmentChip(
      env({ state: "failed", detail: "the mission environment exited" }),
    );
    expect(chip.value).toBe("Failed");
    expect(chip.tone).toBe("red");
    expect(chip.title).toContain("exited");
  });

  it("keeps Starting amber and surfaces what it is waiting for", () => {
    const chip = environmentChip(
      env({ state: "starting", detail: "waiting for the run's subnet router" }),
    );
    expect(chip.value).toBe("Starting");
    expect(chip.tone).toBe("amber");
    expect(chip.title).toContain("subnet router");
  });

  it("reports a torn-down environment as Released", () => {
    const chip = environmentChip(env({ state: "released" }));
    expect(chip.value).toBe("Released");
    expect(chip.tone).toBe("muted");
  });

  it("falls back to Starting while the state is still loading", () => {
    // Never invent a verdict before the server has answered.
    const chip = environmentChip(undefined);
    expect(chip.value).toBe("Starting");
    expect(chip.tone).toBe("amber");
  });
});
