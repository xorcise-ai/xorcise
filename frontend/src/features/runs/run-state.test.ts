import { describe, it, expect } from "vitest";
import { isTerminal, runPresentation } from "./run-state";
import { serverView } from "@/test/fixtures";
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
