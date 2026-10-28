import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { summaryFixture } from "@/test/fixtures";
import { PerformanceSummary, StatTile } from "./performance-summary";

// The Agents list cards: the server's per-agent summary (GET /results/summary), drawn.
describe("PerformanceSummary", () => {
  it("headlines runs, the median and the best scored run", () => {
    render(
      <PerformanceSummary summary={summaryFixture({ runs: 5, median: 0.72, best: 0.95 })} />,
    );
    expect(screen.getByText("Runs")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("Median")).toBeInTheDocument();
    expect(screen.getByText("72%")).toBeInTheDocument();
    expect(screen.getByText("95%")).toBeInTheDocument();
    expect(screen.queryByText("Average")).toBeNull();
  });

  it("rates completion over the agent's attempts, not runs our environment cut short", () => {
    // 4 runs, 1 environment failure: 2 of the 3 attempts completed.
    render(
      <PerformanceSummary
        summary={summaryFixture({ runs: 4, infra_failed: 1, completed: 2 })}
        columns={3}
      />,
    );
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("67%")).toBeInTheDocument();
  });

  it.each([3, 6] as const)(
    "names the timed-out runs in the median and the runs left out (%i columns)",
    (columns) => {
      render(
        <PerformanceSummary
          summary={summaryFixture({ timed_out: 1, no_judge: 2, infra_failed: 1 })}
          columns={columns}
        />,
      );
      expect(screen.getByText("1 timed out: in the median at the score it earned.")).toBeInTheDocument();
      expect(
        screen.getByText("2 no judge · 1 environment failure: not in the median."),
      ).toBeInTheDocument();
    },
  );

  it("renders a stable-size skeleton with no data when loading", () => {
    render(<PerformanceSummary loading columns={3} />);
    expect(screen.getByRole("status", { name: /loading performance summary/i })).toBeInTheDocument();
    expect(screen.queryByText("Median")).toBeNull();
  });
});

describe("StatTile", () => {
  it("renders a single labelled figure", () => {
    render(
      <dl>
        <StatTile label="Median" value="75%" tone="primary" />
      </dl>,
    );
    expect(screen.getByText("Median")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
  });
});
