import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { agentFixture, runFixture, scoreFixture, summaryFixture } from "@/test/fixtures";
import type { ScoreSummary } from "@/lib/api/types";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { FleetMetrics } from "./fleet-metrics";
import { TopAgents, TopMissions } from "./leaderboards";

// Every score on the dashboard is the server's summary (GET /results/summary): the same numbers
// the Results page, the agent pages and `xorcise leaderboard` show. Only the outcome mix and the
// pass rate come from the runs list, by each run's server-given `outcome`.

const RUNS = [
  runFixture({ run_id: "r1", agent_id: "a1", state: "terminal", terminal_trigger: "done" }),
  runFixture({ run_id: "r2", agent_id: "a1", state: "terminal", terminal_trigger: "done" }),
  runFixture({ run_id: "r3", agent_id: "a2", state: "terminal", terminal_trigger: "timeout" }),
];

function serve(summaries: Partial<Record<string, ScoreSummary[]>>, runs = RUNS) {
  server.use(
    http.get("*/api/agents", () =>
      HttpResponse.json([
        agentFixture({ id: "a1", name: "scout" }),
        agentFixture({ id: "a2", name: "probe" }),
      ]),
    ),
    http.get("*/api/runs", () => HttpResponse.json(runs)),
    http.get("*/api/results/runs", () =>
      HttpResponse.json([
        scoreFixture({ run_id: "r1", agent_id: "a1", overall: 0.6 }),
        scoreFixture({ run_id: "r2", agent_id: "a1", overall: 0.8 }),
      ]),
    ),
    http.get("*/api/results/summary", ({ request }) => {
      const by = new URL(request.url).searchParams.get("by") ?? "";
      return HttpResponse.json(summaries[by] ?? []);
    }),
  );
}

describe("Dashboard fleet performance", () => {
  it("FleetMetrics headlines the fleet's median from the server, and the pass rate", async () => {
    serve({ all: [summaryFixture({ runs: 3, scored: 3, median: 0.6, best: 0.8, timed_out: 1 })] });
    renderWithProviders(<FleetMetrics />);
    await waitFor(() => expect(screen.getByText("60%")).toBeInTheDocument());
    expect(screen.getByText("Median")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByText("67%")).toBeInTheDocument(); // Pass rate: 2 of 3 completed
    expect(screen.queryByText("Average")).toBeNull();
    // The timed-out run is in the median, and the dashboard says so.
    expect(
      screen.getByText("1 timed out: in the median at the score it earned."),
    ).toBeInTheDocument();
  });

  it("FleetMetrics keeps infra failures out of the pass rate and buckets the mix by outcome", async () => {
    // #109: a run our infrastructure cut short is not an attempt. Two of them beside the three
    // runs above must leave the pass rate at 2/3, not 2/5, and land in their own segment.
    serve({ all: [summaryFixture({ runs: 5, infra_failed: 2 })] }, [
      ...RUNS,
      runFixture({ run_id: "r4", agent_id: "a1", state: "terminal", terminal_trigger: "crashed" }),
      runFixture({
        run_id: "r5",
        agent_id: "a2",
        state: "terminal",
        terminal_trigger: "deploy_failed",
      }),
    ]);
    renderWithProviders(<FleetMetrics />);
    await waitFor(() => expect(screen.getByText("5")).toBeInTheDocument()); // Evaluated
    expect(screen.getByText("67%")).toBeInTheDocument();
    expect(screen.getByText("Partial").parentElement?.textContent).toBe("Partial1");
    expect(screen.getByText("Infra failed").parentElement?.textContent).toBe("Infra failed2");
    expect(
      await screen.findByText("2 environment failures: not in the median."),
    ).toBeInTheDocument();
  });

  it("TopAgents keeps the server's ranking and hides agents with no scored run", async () => {
    serve({
      agent: [
        summaryFixture({ agent_id: "a3deadbeef", agent_name: "a3deadbe", median: 0.9 }),
        summaryFixture({ agent_id: "a1", agent_name: "scout", median: 0.7 }),
        summaryFixture({ agent_id: "a2", agent_name: "probe", median: null, scored: 0 }),
      ],
    });
    renderWithProviders(<TopAgents />);
    await waitFor(() => expect(screen.getByText("scout")).toBeInTheDocument());
    const rows = screen.getAllByRole("listitem").map((li) => li.textContent ?? "");
    // a3 is a deleted agent: its short id, ranked first on its median.
    expect(rows[0]).toContain("a3deadbe");
    expect(rows[1]).toContain("scout");
    expect(screen.getByText("70%")).toBeInTheDocument();
    expect(screen.queryByText("probe")).toBeNull();
  });

  it("TopMissions lists mission versions, and flags a median that blends assisted runs", async () => {
    serve({
      mission: [
        summaryFixture({
          mission: "c1",
          mission_version: "2.0.0",
          current_version: true,
          runs: 3,
          scored: 2,
          assisted: 1,
        }),
        summaryFixture({ mission: "c1", mission_version: "1.0.0", current_version: false }),
      ],
    });
    renderWithProviders(<TopMissions />);
    await waitFor(() => expect(screen.getByText("c1 v2.0.0")).toBeInTheDocument());
    expect(screen.getByText("c1 v1.0.0 (older)")).toBeInTheDocument();
    expect(screen.getByText("median blends 1/2 assisted")).toBeInTheDocument();
  });

  it("says when runs with no judge are left out of an agent's median", async () => {
    serve({ agent: [summaryFixture({ agent_id: "a1", no_judge: 2 })] });
    renderWithProviders(<TopAgents />);
    expect(await screen.findByText("2 with no judge left out")).toBeInTheDocument();
  });
});
