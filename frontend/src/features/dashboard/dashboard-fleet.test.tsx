import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { agentFixture, runFixture } from "@/test/fixtures";

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

// scout: two runs finished on-terms (0.8, 0.6) → scored. probe: one timeout (partial, excluded).
const RUNS = [
  runFixture({
    run_id: "r1",
    agent_id: "a1",
    mission: "c1",
    state: "terminal",
    terminal_trigger: "done",
    completed_at: "2026-06-29T10:05:00Z",
  }),
  runFixture({
    run_id: "r2",
    agent_id: "a1",
    mission: "c2",
    state: "terminal",
    terminal_trigger: "done",
    completed_at: "2026-06-29T11:05:00Z",
  }),
  runFixture({
    run_id: "r3",
    agent_id: "a2",
    mission: "c1",
    state: "terminal",
    terminal_trigger: "timeout",
    completed_at: "2026-06-29T12:05:00Z",
  }),
];

const RESULT: Record<string, { overall: number; partial: boolean }> = {
  r1: { overall: 0.8, partial: false },
  r2: { overall: 0.6, partial: false },
  r3: { overall: 0.1, partial: true },
};

function handlers() {
  server.use(
    http.get("*/api/agents", () =>
      HttpResponse.json([
        agentFixture({ id: "a1", name: "scout" }),
        agentFixture({ id: "a2", name: "probe" }),
      ]),
    ),
    http.get("*/api/missions", () => HttpResponse.json([])),
    http.get("*/api/runs", () => HttpResponse.json(RUNS)),
    http.get("*/api/runs/:id/result", ({ params }) => {
      const r = RESULT[String(params.id)];
      return r
        ? HttpResponse.json({
            grade: { run_id: params.id, overall: r.overall },
            conditions: {},
            partial: r.partial,
          })
        : new HttpResponse(null, { status: 404 });
    }),
  );
}

describe("Dashboard fleet performance", () => {
  it("FleetMetrics averages only non-partial scores and shows the pass rate", async () => {
    handlers();
    renderWithProviders(<FleetMetrics />);
    // Average of the two on-terms runs (0.8, 0.6) = 70%; best 80%; 3 evaluated; pass = 2/3 = 67%.
    await waitFor(() => expect(screen.getByText("70%")).toBeInTheDocument());
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument(); // Evaluated
    expect(screen.getByText("67%")).toBeInTheDocument(); // Pass rate
  });

  it("TopAgents ranks scored agents and hides agents with no scored run", async () => {
    handlers();
    renderWithProviders(<TopAgents />);
    await waitFor(() => expect(screen.getByText("scout")).toBeInTheDocument());
    expect(screen.getByText("70%")).toBeInTheDocument();
    // probe's only run is a partial timeout → no scored run → excluded from the leaderboard.
    expect(screen.queryByText("probe")).toBeNull();
  });

  it("TopMissions flags a mission whose average blends assisted runs", async () => {
    // c1: two scored runs, one of them disclosed intel → the single average mixes assisted +
    // unassisted, which the row must flag so it isn't read as like-for-like.
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ id: "a1", name: "scout" })]),
      ),
      http.get("*/api/missions", () => HttpResponse.json([])),
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({
            run_id: "r1",
            agent_id: "a1",
            mission: "c1",
            state: "terminal",
            terminal_trigger: "done",
            completed_at: "2026-06-29T10:05:00Z",
          }),
          runFixture({
            run_id: "r2",
            agent_id: "a1",
            mission: "c1",
            state: "terminal",
            terminal_trigger: "done",
            completed_at: "2026-06-29T11:05:00Z",
          }),
        ]),
      ),
      http.get("*/api/runs/:id/result", ({ params }) => {
        const map: Record<string, { overall: number; intel: number }> = {
          r1: { overall: 0.8, intel: 0 }, // unassisted
          r2: { overall: 0.6, intel: 2 }, // assisted
        };
        const r = map[String(params.id)];
        return r
          ? HttpResponse.json({
              grade: { run_id: params.id, overall: r.overall },
              conditions: { intel_disclosed: r.intel },
              partial: false,
            })
          : new HttpResponse(null, { status: 404 });
      }),
    );
    renderWithProviders(<TopMissions />);
    await waitFor(() => expect(screen.getByText("c1")).toBeInTheDocument());
    expect(screen.getByText("avg blends 1/2 assisted")).toBeInTheDocument();
  });
});
