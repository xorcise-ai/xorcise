import { describe, it, expect, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { agentFixture, scoreFixture, summaryFixture } from "@/test/fixtures";
import type { RunScore, ScoreSummary } from "@/lib/api/types";

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

import { CompletedRuns } from "./completed-runs";

function serve(scores: RunScore[], summaries: ScoreSummary[]) {
  server.use(
    http.get("*/api/agents", () =>
      HttpResponse.json([
        agentFixture({ id: "agent-1", name: "scout" }),
        agentFixture({ id: "agent-2", name: "raptor" }),
      ]),
    ),
    http.get("*/api/results/runs", () => HttpResponse.json(scores)),
    http.get("*/api/results/summary", () => HttpResponse.json(summaries)),
  );
}

const cards = () => screen.getAllByRole("img").map((s) => s.getAttribute("aria-label"));

describe("CompletedRuns: the run strip (agent-centric §14)", () => {
  it("ranks agents by the median of their scored runs, unscored last", async () => {
    serve(
      [],
      [
        summaryFixture({ agent_id: "agent-1", median: 0.6 }),
        summaryFixture({ agent_id: "agent-2", median: 0.8 }),
        summaryFixture({ agent_id: "ghost", median: null, scored: 0 }),
      ],
    );
    renderWithProviders(<CompletedRuns />);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(3));
    expect(cards().map((l) => l?.split(":")[0])).toEqual(["raptor", "scout", "ghost"]);
  });

  it("headlines the median and how many runs it covers, and links to the profile", async () => {
    serve(
      [scoreFixture({ run_id: "s1" })],
      [summaryFixture({ agent_id: "agent-1", runs: 4, scored: 3, median: 0.75 })],
    );
    renderWithProviders(<CompletedRuns />);
    const card = (await screen.findByText("scout")).closest("a")!;
    expect(card).toHaveAttribute("href", "/agents/detail?name=scout");
    expect(within(card).getByText("Median")).toBeInTheDocument();
    expect(within(card).getAllByText("75%").length).toBeGreaterThan(0);
    expect(within(card).getByText("3 of 4")).toBeInTheDocument();
  });

  it("says which runs are left out of the median, and why", async () => {
    serve(
      [],
      [
        summaryFixture({
          agent_id: "agent-1",
          runs: 6,
          scored: 2,
          no_judge: 2,
          stopped: 1,
          timed_out: 1,
          infra_failed: 1,
          partial_judge: 1,
        }),
      ],
    );
    renderWithProviders(<CompletedRuns />);
    expect(
      await screen.findByText(
        "2 no judge · 1 stopped · 1 environment failure: not in the median.",
      ),
    ).toBeInTheDocument();
    // A timed-out run is IN the median: said apart from the runs that are not.
    expect(
      screen.getByText("1 timed out: in the median at the score it earned."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("1 judged on part of the rubric, counted at the lower bound."),
    ).toBeInTheDocument();
    expect(cards()[0]).toContain("not in the median");
  });

  it("draws each kind of run by what was measured", async () => {
    serve(
      [
        scoreFixture({ run_id: "full" }),
        scoreFixture({
          run_id: "part",
          judge_status: "partial",
          judge: 0.4,
          judge_upper: 0.9,
          overall: 0.7,
          overall_upper: 0.95,
        }),
        scoreFixture({
          run_id: "nojudge",
          bucket: "no_judge",
          judge_status: "unavailable",
          judge: 0,
          overall: 0.5,
        }),
        scoreFixture({
          run_id: "stopped",
          bucket: "stopped",
          outcome: "partial",
          terminal_trigger: "operator",
        }),
        scoreFixture({
          run_id: "timeout",
          timed_out: true,
          outcome: "partial",
          terminal_trigger: "timeout",
        }),
        scoreFixture({
          run_id: "env",
          bucket: "infra_failed",
          outcome: "infra_failed",
          deterministic: null,
          judge: null,
          judge_status: null,
          overall: null,
        }),
      ],
      [summaryFixture({ agent_id: "agent-1" })],
    );
    renderWithProviders(<CompletedRuns />);
    await waitFor(() => expect(screen.getAllByTestId("strip-column")).toHaveLength(6));
    const [full, part, nojudge, stopped, timedOut, env] = screen.getAllByTestId("strip-column");
    expect(full.querySelector("[data-part]")).toBeNull();
    expect(part.querySelector('[data-part="judge-range"]')).not.toBeNull();
    // The judge half that was never measured is an outline, not a zero.
    expect(nojudge.querySelector('[data-part="unmeasured"]')).not.toBeNull();
    expect(nojudge.title).toContain("no judge");
    // Stopped by an operator: out of the median, so faded.
    expect(stopped.className).toContain("opacity-40");
    expect(stopped.title).toContain("stopped by an operator");
    // Timed out: in the median at what it earned, so drawn in full and marked, not faded.
    expect(timedOut.className).not.toContain("opacity-40");
    expect(timedOut.title).toContain("timed out");
    expect(timedOut.title).not.toContain("not in the median");
    // An environment failure is an empty slot, never a score.
    expect(env.children).toHaveLength(0);
    expect(env.title).toContain("environment failure");
  });

  it("shows the latest runs that fit, and says how many there are in all", async () => {
    const runs = Array.from({ length: 30 }, (_, i) => scoreFixture({ run_id: `r${i}` }));
    serve(runs, [summaryFixture({ agent_id: "agent-1", runs: 30, scored: 30 })]);
    renderWithProviders(<CompletedRuns />);
    // jsdom cannot measure, so the strip draws its fallback of 24.
    expect(await screen.findByText("latest 24 of 30")).toBeInTheDocument();
    expect(screen.getAllByTestId("strip-column")).toHaveLength(24);
  });

  it("draws the median line at the median", async () => {
    serve([scoreFixture()], [summaryFixture({ agent_id: "agent-1", median: 0.62 })]);
    renderWithProviders(<CompletedRuns />);
    const line = await screen.findByTestId("strip-median");
    expect(line.style.bottom).toBe("62%");
  });

  it("shows a what/why/next empty state with Start run when nothing has finished", async () => {
    serve([], []);
    renderWithProviders(<CompletedRuns />);
    await waitFor(() => expect(screen.getByText(/No results yet/i)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /Start run/i })).toHaveAttribute("href", "/runs/new");
  });
});
