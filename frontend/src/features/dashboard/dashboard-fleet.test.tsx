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
    outcome: "completed",
    completed_at: "2026-06-29T10:05:00Z",
  }),
  runFixture({
    run_id: "r2",
    agent_id: "a1",
    mission: "c2",
    state: "terminal",
    terminal_trigger: "done",
    outcome: "completed",
    completed_at: "2026-06-29T11:05:00Z",
  }),
  runFixture({
    run_id: "r3",
    agent_id: "a2",
    mission: "c1",
    state: "terminal",
    terminal_trigger: "timeout",
    outcome: "partial",
    completed_at: "2026-06-29T12:05:00Z",
  }),
];

const RESULT: Record<string, { overall: number; partial: boolean; judge_status?: string }> = {
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
            grade: { run_id: params.id, overall: r.overall, judge_status: r.judge_status },
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

  it("FleetMetrics keeps infra failures out of the pass rate and buckets the mix by outcome", async () => {
    // #109: a run our infrastructure cut short is not an attempt. Two of them beside the three
    // runs above must leave the pass rate at 2/3 — not 2/5 — and land in their own segment.
    // The timeout is a PARTIAL run: it used to fall in "Failed" because the mix keyed on the
    // status tone (red), which left the Partial segment empty for every fleet.
    handlers();
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          ...RUNS,
          runFixture({ run_id: "r4", agent_id: "a1", state: "terminal", terminal_trigger: "crashed" }),
          runFixture({
            run_id: "r5",
            agent_id: "a2",
            state: "terminal",
            terminal_trigger: "deploy_failed",
          }),
        ]),
      ),
    );
    renderWithProviders(<FleetMetrics />);
    await waitFor(() => expect(screen.getByText("5")).toBeInTheDocument()); // Evaluated
    expect(screen.getByText("67%")).toBeInTheDocument(); // Pass rate over the 3 attempts
    expect(screen.getByText("Partial").parentElement?.textContent).toBe("Partial1");
    expect(screen.getByText("Infra failed").parentElement?.textContent).toBe("Infra failed2");
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
            outcome: "completed",
            completed_at: "2026-06-29T10:05:00Z",
          }),
          runFixture({
            run_id: "r2",
            agent_id: "a1",
            mission: "c1",
            state: "terminal",
            terminal_trigger: "done",
            outcome: "completed",
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

  it("discloses the runs in an average that were scored with no judge (#143)", async () => {
    // r1 had no judge; r3 had none either, but it timed out, so it is in no average.
    RESULT.r1.judge_status = "unavailable";
    RESULT.r3.judge_status = "unavailable";
    try {
      handlers();
      renderWithProviders(
        <>
          <FleetMetrics />
          <TopAgents />
        </>,
      );
      expect(
        await screen.findByText(
          "No judge on 1 of 2 scored runs: scored at 50% at most, and included in Average and Best.",
        ),
      ).toBeInTheDocument();
      // scout (r1, r2) carries the flag; probe's only run is partial, so it is not ranked.
      expect(screen.getByText("avg includes 1 with no judge")).toBeInTheDocument();
    } finally {
      delete RESULT.r1.judge_status;
      delete RESULT.r3.judge_status;
    }
  });
});
