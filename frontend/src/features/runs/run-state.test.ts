import { describe, it, expect } from "vitest";
import { runStateMeta, isTerminal, runPresentation } from "./run-state";

describe("runStateMeta", () => {
  it("maps created and active", () => {
    expect(runStateMeta("created")).toEqual({
      label: "created",
      variant: "default",
    });
    expect(runStateMeta("active")).toEqual({
      label: "running",
      variant: "default",
    });
  });

  it("maps terminal outcomes by trigger", () => {
    expect(runStateMeta("terminal", "done")).toEqual({
      label: "completed",
      variant: "ok",
    });
    expect(runStateMeta("terminal", "error")).toEqual({
      label: "error",
      variant: "err",
    });
    expect(runStateMeta("terminal", "timeout").variant).toBe("err");
    expect(runStateMeta("terminal", null)).toEqual({
      label: "terminal",
      variant: "muted",
    });
  });

  it("falls back gracefully for unknown states", () => {
    expect(runStateMeta("weird")).toEqual({ label: "weird", variant: "muted" });
  });

  it("marks a failed deploy as an error, not an anonymous terminal", () => {
    expect(runStateMeta("terminal", "deploy_failed").variant).toBe("err");
  });

  it("isTerminal detects terminal runs", () => {
    expect(isTerminal({ state: "terminal" })).toBe(true);
    expect(isTerminal({ state: "active" })).toBe(false);
  });
});

describe("runPresentation deploy_failed", () => {
  it("reads as a red 'Deploy failed', not a muted 'Terminal'", () => {
    // The readiness gate closes out a run whose environment never came up. That is a FAILURE the
    // operator must see — falling through to the muted catch-all would read as a benign end.
    const view = runPresentation("terminal", "deploy_failed");
    expect(view.label).toBe("Deploy failed");
    expect(view.tone).toBe("red");
  });

  it("sends the operator to the run rather than a result that never existed", () => {
    // The environment never came up, so there is nothing graded to open.
    expect(runPresentation("terminal", "deploy_failed").action).toEqual({
      label: "Inspect Run",
      target: "live",
    });
  });
});
