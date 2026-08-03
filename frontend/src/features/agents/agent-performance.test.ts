import { describe, it, expect } from "vitest";
import { summarize } from "./agent-performance";
import type { AgentHistoryEntry } from "@/lib/api/types";

const h = (over: Record<string, unknown>): AgentHistoryEntry =>
  ({
    agent_id: "a1",
    run_id: "r",
    overall: null,
    deterministic: null,
    judge: null,
    partial: false,
    partial_trigger: null,
    conditions: null,
    trace_ref: null,
    created_at: "2026-06-01T00:00:00Z",
    ...over,
  }) as unknown as AgentHistoryEntry;

describe("summarize", () => {
  it("excludes partial runs from the score aggregate but still counts them", () => {
    // A partial run (budget timeout or operator/manual kill) did not end on the agent's own
    // terms, so its score must not drag the track record down — but it still counts toward the
    // run total and the partial rate.
    const s = summarize([
      h({ run_id: "a", overall: 0.4, judge: 0.5, deterministic: 0.3, partial: true }),
      h({ run_id: "b", overall: 0.8, judge: 0.7, deterministic: 0.9 }),
    ]);
    expect(s.runs).toBe(2);
    expect(s.scored).toBe(1); // only the non-partial run counts toward the score
    expect(s.avgOverall).toBeCloseTo(0.8);
    expect(s.bestOverall).toBe(0.8);
    expect(s.avgJudge).toBeCloseTo(0.7);
    expect(s.avgDeterministic).toBeCloseTo(0.9);
    expect(s.partialRate).toBe(0.5);
  });

  it("excludes partial runs from the trend", () => {
    const s = summarize([
      h({ run_id: "ok", overall: 0.9, created_at: "2026-06-02T00:00:00Z" }),
      h({
        run_id: "killed",
        overall: 0.0,
        partial: true,
        partial_trigger: "operator",
        created_at: "2026-06-03T00:00:00Z",
      }),
    ]);
    expect(s.trend).toEqual([0.9]);
  });

  it("ignores null scores in the averages but still counts the run", () => {
    const s = summarize([
      h({ run_id: "a", overall: null }),
      h({ run_id: "b", overall: 1.0 }),
    ]);
    expect(s.runs).toBe(2);
    expect(s.scored).toBe(1);
    expect(s.avgOverall).toBe(1.0);
  });

  it("orders the trend oldest → newest by created_at", () => {
    const s = summarize([
      h({ run_id: "new", overall: 0.9, created_at: "2026-06-03T00:00:00Z" }),
      h({ run_id: "old", overall: 0.1, created_at: "2026-06-01T00:00:00Z" }),
    ]);
    expect(s.trend).toEqual([0.1, 0.9]);
  });

  it("is safe on an empty history", () => {
    const s = summarize([]);
    expect(s).toMatchObject({
      runs: 0,
      scored: 0,
      avgOverall: null,
      bestOverall: null,
      partialRate: 0,
      trend: [],
    });
  });
});
