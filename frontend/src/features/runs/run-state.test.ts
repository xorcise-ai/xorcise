import { describe, it, expect } from "vitest";
import { isTerminal, runPresentation, classifyRun } from "./run-state";
import { serverView } from "@/test/fixtures";
import { summarizeByAgent } from "@/features/results/summarize-runs";
import { summarizeByMission } from "@/features/results/summarize-missions";
import type { RunEntry } from "@/lib/api/types";

describe("isTerminal", () => {
  it("detects terminal runs", () => {
    expect(isTerminal({ state: "terminal" })).toBe(true);
    expect(isTerminal({ state: "active" })).toBe(false);
  });
});

const run = (state: string, trigger: RunEntry["terminal_trigger"] = null) => ({
  state,
  ...serverView(state, trigger),
});

// #165: the GUI renders the server's status (RunEntry.status) and holds no trigger table. These
// pin what it does WITH that status: the tone palette and the primary action per outcome.
describe("runPresentation", () => {
  it.each([
    ["created", null, "amber", { label: "Open live run", target: "live" }],
    ["active", null, "amber", { label: "Open live run", target: "live" }],
    ["terminal", "done", "green", { label: "View result", target: "result" }],
    ["terminal", "timeout", "red", { label: "View partial result", target: "result" }],
    // An operator stop is graded partial, so it has a result to open — it used to send the
    // operator to the live view as though there were none.
    ["terminal", "operator", "muted", { label: "View partial result", target: "result" }],
    // An environment failure is never graded: inspect the run, there is no result.
    ["terminal", "deploy_failed", "red", { label: "Inspect run", target: "live" }],
    ["terminal", "crashed", "red", { label: "Inspect run", target: "live" }],
    ["terminal", null, "muted", { label: "Inspect run", target: "live" }],
  ] as const)("%s/%s → %s, %o", (state, trigger, tone, action) => {
    const view = runPresentation(run(state, trigger));
    expect(view.label).toBe(serverView(state, trigger).status.label);
    expect(view.tone).toBe(tone);
    expect(view.action).toEqual(action);
  });

  it("shows a crash as the red failure it is, not a muted 'Terminal'", () => {
    // The GUI used to fall through to a grey "Terminal" for crashed runs while `run list` said
    // "Crashed" — the two surfaces disagreed about the same run.
    const view = runPresentation(run("terminal", "crashed"));
    expect(view.label).toBe("Crashed");
    expect(view.tone).toBe("red");
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
