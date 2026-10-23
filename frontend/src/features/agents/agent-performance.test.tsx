import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { summaryFixture } from "@/test/fixtures";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { AgentPerformance } from "./agent-performance";

// The agent page's headline is the server's summary (GET /results/summary?by=agent): the same
// numbers the agent's card on Results › Agents, the dashboard and `xorcise leaderboard` show.
describe("AgentPerformance", () => {
  it("says the agent has no finished run when the server has no summary, not loading forever", () => {
    render(<AgentPerformance summary={undefined} summaryState="ready" ledger={[]} />);
    expect(screen.getAllByText("No finished runs yet.")).toHaveLength(2);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("says so when the summary failed to load", () => {
    render(<AgentPerformance summary={undefined} summaryState="error" ledger={[]} />);
    expect(screen.getByText("Couldn’t load this agent’s performance.")).toBeInTheDocument();
  });

  it("headlines the median and the averages of the same scored runs, from the server", () => {
    render(
      <AgentPerformance
        summary={summaryFixture({
          runs: 6,
          scored: 4,
          median: 0.75,
          best: 0.9,
          mean_deterministic: 0.8,
          mean_judge: 0.6,
        })}
        ledger={[]}
      />,
    );
    expect(screen.getByText("Median")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("4 of 6")).toBeInTheDocument();
    expect(screen.getByText("Avg deterministic")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByText("60%")).toBeInTheDocument();
  });

  it("says that timed-out runs are in the median, and which runs are not", () => {
    render(
      <AgentPerformance
        summary={summaryFixture({ timed_out: 2, no_judge: 1, stopped: 1 })}
        ledger={[]}
      />,
    );
    expect(
      screen.getByText("2 timed out: in the median at the score they earned."),
    ).toBeInTheDocument();
    expect(screen.getByText("1 no judge · 1 stopped: not in the median.")).toBeInTheDocument();
  });

  it("shows no judge average when no run was judged in full", () => {
    render(<AgentPerformance summary={summaryFixture({ mean_judge: null })} ledger={[]} />);
    expect(screen.queryByText("Avg judge")).toBeNull();
  });
});
