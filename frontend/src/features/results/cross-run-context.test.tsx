import { describe, it, expect } from "vitest";
import { summarizeRuns } from "./summarize-runs";

describe("summarizeRuns", () => {
  it("averages and picks best over non-partial scored runs", () => {
    const s = summarizeRuns([
      { overall: 0.6, partial: false, when: "2026-01-01" },
      { overall: 0.8, partial: false, when: "2026-01-02" },
    ]);
    expect(s.n).toBe(2);
    expect(s.avgOverall).toBeCloseTo(0.7);
    expect(s.bestOverall).toBe(0.8);
    expect(s.trend).toEqual([0.6, 0.8]);
  });

  it("excludes partial runs from the baseline", () => {
    const s = summarizeRuns([
      { overall: 0.9, partial: false, when: "2026-01-01" },
      { overall: 0.1, partial: true, when: "2026-01-02" }, // timed out — must not drag avg
    ]);
    expect(s.n).toBe(1);
    expect(s.avgOverall).toBe(0.9);
    expect(s.bestOverall).toBe(0.9);
  });

  it("orders the trend oldest → newest regardless of input order", () => {
    const s = summarizeRuns([
      { overall: 0.5, partial: false, when: "2026-03-03" },
      { overall: 0.2, partial: false, when: "2026-01-01" },
      { overall: 0.9, partial: false, when: "2026-02-02" },
    ]);
    expect(s.trend).toEqual([0.2, 0.9, 0.5]);
  });

  it("is empty when there are no scored non-partial runs", () => {
    const s = summarizeRuns([{ overall: null, partial: false, when: "2026-01-01" }]);
    expect(s.n).toBe(0);
    expect(s.avgOverall).toBeNull();
    expect(s.bestOverall).toBeNull();
    expect(s.trend).toEqual([]);
  });
});
