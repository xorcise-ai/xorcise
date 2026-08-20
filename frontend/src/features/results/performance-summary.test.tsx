import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { summarizeByAgent, type AgentRunRow } from "./summarize-runs";
import { PerformanceSummary, StatTile } from "./performance-summary";

const row = (over: Partial<AgentRunRow> = {}): AgentRunRow => ({
  agentId: "a1",
  agentName: "scout",
  overall: 0.5,
  partial: false,
  completed: true,
  when: "2026-01-01T00:00:00Z",
  ...over,
});

describe("summarizeByAgent", () => {
  it("groups runs per agent and ranks best-average first", () => {
    const out = summarizeByAgent([
      row({ agentId: "a1", agentName: "scout", overall: 0.4 }),
      row({ agentId: "a1", agentName: "scout", overall: 0.6 }),
      row({ agentId: "a2", agentName: "raptor", overall: 0.9 }),
    ]);
    expect(out.map((s) => s.agentName)).toEqual(["raptor", "scout"]);
    const scout = out.find((s) => s.agentId === "a1")!;
    expect(scout.runs).toBe(2);
    expect(scout.avgOverall).toBeCloseTo(0.5);
    expect(scout.bestOverall).toBeCloseTo(0.6);
  });

  it("excludes partial runs from score aggregates but still counts them", () => {
    const out = summarizeByAgent([
      row({ overall: 0.8, partial: false, completed: true }),
      row({ overall: 0.2, partial: true, completed: false, when: "2026-02-01T00:00:00Z" }),
    ]);
    const s = out[0];
    expect(s.runs).toBe(2);
    expect(s.scored).toBe(1);
    expect(s.avgOverall).toBeCloseTo(0.8); // the partial 0.2 is not counted
    expect(s.bestOverall).toBeCloseTo(0.8);
    expect(s.completionRate).toBeCloseTo(0.5);
    expect(s.partialRate).toBeCloseTo(0.5);
    expect(s.lastRun).toBe("2026-02-01T00:00:00Z"); // most recent
  });

  it("yields null score aggregates when no run is scored", () => {
    const out = summarizeByAgent([row({ overall: null })]);
    expect(out[0].avgOverall).toBeNull();
    expect(out[0].bestOverall).toBeNull();
  });
});

describe("PerformanceSummary", () => {
  it("renders the §14 headline fields (props-driven, no fetching)", () => {
    render(
      <PerformanceSummary
        summary={{
          agentId: "a1",
          agentName: "scout",
          runs: 3,
          scored: 2,
          avgOverall: 0.75,
          bestOverall: 0.9,
          completionRate: 0.66,
          partialRate: 0.33,
          lastRun: "2026-01-01T12:00:00Z",
        }}
      />,
    );
    expect(screen.getByText("Runs")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Average")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("Best")).toBeInTheDocument();
    expect(screen.getByText("90%")).toBeInTheDocument();
    expect(screen.getByText("Completion")).toBeInTheDocument();
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(screen.getByText("Last run")).toBeInTheDocument();
  });

  it("renders the same headline tiles for a single agent (5.1/5.3 reuse)", () => {
    const [only] = summarizeByAgent([
      row({ agentId: "solo", agentName: "vega", overall: 0.5, completed: true }),
    ]);
    render(<PerformanceSummary summary={only} />);
    // A single-agent summary renders exactly like one aggregate row.
    expect(screen.getByText("Runs")).toBeInTheDocument();
    expect(screen.getByText("Average")).toBeInTheDocument();
    // 50% is both this agent's Average and Best (single scored run).
    expect(screen.getAllByText("50%").length).toBeGreaterThanOrEqual(2);
  });

  it("renders a stable-size skeleton with no data when loading", () => {
    const { container } = render(<PerformanceSummary loading />);
    // Loading state announces itself and renders no headline labels/values yet.
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText("Runs")).not.toBeInTheDocument();
    // Six placeholder tiles (label + value skeleton each), matching the six data tiles →
    // grid never reflows on load.
    expect(container.querySelectorAll(".skeleton-shimmer")).toHaveLength(12);
  });
});

describe("StatTile", () => {
  it("renders a single labelled figure", () => {
    render(
      <dl>
        <StatTile label="Average" value="75%" tone="primary" />
      </dl>,
    );
    expect(screen.getByText("Average")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
  });
});
