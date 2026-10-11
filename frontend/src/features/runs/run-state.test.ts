import { describe, it, expect } from "vitest";
import { runStateMeta, isTerminal, runPresentation, classifyRun } from "./run-state";
import { summarizeByAgent } from "@/features/results/summarize-runs";
import { summarizeByMission } from "@/features/results/summarize-missions";
import type { RunEntry } from "@/lib/api/types";

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
      label: "Inspect run",
      target: "live",
    });
  });
});

// #159: the GUI's aggregates read the server's classification (RunEntry.outcome) and never derive
// it from the trigger. Four private copies of the trigger rule once listed a "budget" trigger
// nothing writes and missed "operator", so an operator kill counted as genuine while grading. The
// trigger → outcome mapping itself is tested on the server (test_run_terminate.py).
describe("classifyRun", () => {
  it.each([
    ["completed", { partial: false, completed: true, infraFailed: false }],
    ["partial", { partial: true, completed: false, infraFailed: false }],
    ["infra_failed", { partial: false, completed: false, infraFailed: true }],
    [null, { partial: false, completed: false, infraFailed: false }],
  ] as const)("spreads the server outcome %s", (outcome, expected) => {
    expect(classifyRun(outcome)).toEqual(expected);
  });
});

// #109: a run our infrastructure cut short is not one of the agent's attempts — it stays in Runs,
// is disclosed as infraFailed, and moves neither score nor rate.
describe("environment failures in the aggregates", () => {
  const row = (outcome: RunEntry["outcome"], overall: number | null) => ({
    agentId: "a1",
    agentName: "alpha",
    mission: "m1",
    overall,
    ...classifyRun(outcome),
    when: "2026-10-09T00:00:00Z",
  });
  const rows = [
    row("completed", 0.8),
    row("partial", 0.2),
    row("infra_failed", null),
    row("infra_failed", null),
  ];

  it("leaves them out of the agent rates but in Runs", () => {
    const [s] = summarizeByAgent(rows);
    expect(s.runs).toBe(4);
    expect(s.infraFailed).toBe(2);
    expect(s.completionRate).toBe(0.5);
    expect(s.partialRate).toBe(0.5);
    expect(s.avgOverall).toBe(0.8);
  });

  it("does the same per mission", () => {
    const [s] = summarizeByMission(rows);
    expect(s.runs).toBe(4);
    expect(s.infraFailed).toBe(2);
    expect(s.completionRate).toBe(0.5);
    expect(s.partialRate).toBe(0.5);
  });
});
