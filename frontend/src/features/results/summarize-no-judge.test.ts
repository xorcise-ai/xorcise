import { describe, it, expect } from "vitest";
import { judgeDegraded, summarizeByAgent, type AgentRunRow } from "./summarize-runs";
import { summarizeByMission } from "./summarize-missions";

// #143: the GUI averages disclose runs scored with no judge, as the CLI leaderboard does (#133).

describe("judgeDegraded", () => {
  it.each([
    ["unavailable", true],
    ["model-not-configured", true],
    // The judge ran on part of the rubric and discloses its own coverage: not "no judge".
    ["partial", false],
    ["ok", false],
    // No status is an ungraded run, not a degraded one.
    [null, false],
    [undefined, false],
  ])("%s → %s", (status, expected) => {
    expect(judgeDegraded(status)).toBe(expected);
  });
});

const row = (over: Partial<AgentRunRow> = {}): AgentRunRow => ({
  agentId: "a1",
  agentName: "scout",
  overall: 0.5,
  partial: false,
  completed: true,
  when: "2026-01-01T00:00:00Z",
  ...over,
});

describe("no-judge counts in the per-agent and per-mission summaries", () => {
  it("counts over the same cohort as Average and Best, never every row", () => {
    // The CLI once counted every row, and a timed-out run read "No judge 1" beside "Avg —".
    const [s] = summarizeByAgent([
      row({ judgeDegraded: true }),
      row({ overall: 0.9 }),
      row({ partial: true, completed: false, judgeDegraded: true }),
    ]);
    expect(s.scored).toBe(2);
    expect(s.noJudge).toBe(1);
  });

  it("is zero for an agent whose only unjudged run timed out", () => {
    const [s] = summarizeByAgent([row({ partial: true, completed: false, judgeDegraded: true })]);
    expect(s.avgOverall).toBeNull();
    expect(s.noJudge).toBe(0);
  });

  it("applies the same rule per mission", () => {
    const [m] = summarizeByMission([
      { mission: "c1", overall: 0.5, partial: false, completed: true, judgeDegraded: true, when: "" },
      { mission: "c1", overall: 0.5, partial: true, completed: false, judgeDegraded: true, when: "" },
    ]);
    expect(m.scored).toBe(1);
    expect(m.noJudge).toBe(1);
  });
});
