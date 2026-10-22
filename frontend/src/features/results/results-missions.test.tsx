import { describe, it, expect, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { agentFixture, scoreFixture, summaryFixture } from "@/test/fixtures";
import type { RunScore, ScoreSummary } from "@/lib/api/types";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { ResultsByMission, gapWords } from "./results-missions";

function serve(scores: RunScore[], versions: ScoreSummary[], perAgent: ScoreSummary[] = []) {
  server.use(
    http.get("*/api/agents", () =>
      HttpResponse.json([
        agentFixture({ id: "agent-1", name: "scout" }),
        agentFixture({ id: "agent-2", name: "recon" }),
      ]),
    ),
    http.get("*/api/results/runs", () => HttpResponse.json(scores)),
    http.get("*/api/results/summary", ({ request }) => {
      const by = new URL(request.url).searchParams.get("by");
      return HttpResponse.json(by === "mission" ? versions : perAgent);
    }),
  );
}

const version = (v: string, current: boolean, over: Partial<ScoreSummary> = {}) =>
  summaryFixture({ mission: "sqli-login", mission_version: v, current_version: current, ...over });

describe("ResultsByMission: grader agreement per mission version", () => {
  it("gives an older mission version its own card, marked as older", async () => {
    serve(
      [
        scoreFixture({ run_id: "new", mission_version: "2.0.0" }),
        scoreFixture({ run_id: "old", mission_version: "1.0.0", current_version: false }),
      ],
      [version("1.0.0", false, { last_run: "2026-06-01T00:00:00Z" }), version("2.0.0", true)],
    );
    renderWithProviders(<ResultsByMission />);
    await waitFor(() => expect(screen.getAllByTestId("mission-version-card")).toHaveLength(2));
    const [current, older] = screen.getAllByTestId("mission-version-card");
    // The current version comes first; only the older one is marked.
    expect(within(current).getByText("v2.0.0")).toBeInTheDocument();
    expect(within(current).queryByText("older")).toBeNull();
    expect(within(older).getByText("v1.0.0")).toBeInTheDocument();
    expect(within(older).getByText("older")).toBeInTheDocument();
    // Each plot holds only its own version's runs.
    expect(within(current).getAllByTestId("agreement-point")).toHaveLength(1);
    expect(within(older).getAllByTestId("agreement-point")).toHaveLength(1);
  });

  it("places scored runs by checks and judge, and puts no-judge runs on the rail", async () => {
    serve(
      [
        scoreFixture({ run_id: "a", deterministic: 1, judge: 0.3, overall: 0.65 }),
        scoreFixture({ run_id: "b", bucket: "no_judge", judge_status: "unavailable", judge: 0 }),
        scoreFixture({ run_id: "c", bucket: "ended_early", outcome: "partial", terminal_trigger: "operator" }),
        scoreFixture({
          run_id: "d",
          bucket: "infra_failed",
          outcome: "infra_failed",
          deterministic: null,
          judge: null,
          overall: null,
          judge_status: null,
        }),
      ],
      [version("1.0.0", true, { runs: 4, scored: 1, no_judge: 1, ended_early: 1, infra_failed: 1 })],
    );
    renderWithProviders(<ResultsByMission />);
    await waitFor(() => expect(screen.getAllByTestId("agreement-point")).toHaveLength(2));
    const [scored, early] = screen.getAllByTestId("agreement-point");
    expect(scored.style.left).toContain("100%");
    expect(scored.style.bottom).toBe("30%");
    expect(early.dataset.bucket).toBe("ended_early");
    fireEvent.mouseEnter(early);
    expect(screen.getByTestId("agreement-hover")).toHaveTextContent("stopped by an operator");
    fireEvent.mouseLeave(early);
    // The no-judge run has no judge score to place: it sits on the rail at its checks score.
    expect(screen.getAllByTestId("agreement-rail")).toHaveLength(1);
    // The environment failure is counted, never placed.
    expect(
      screen.getByText("1 no judge · 1 timed out or stopped · 1 environment failure: not in the median."),
    ).toBeInTheDocument();
  });

  it("names each agent in a chip, with its median and judge gap, and no table", async () => {
    serve(
      [
        scoreFixture({ run_id: "s", agent_id: "agent-1" }),
        scoreFixture({ run_id: "r", agent_id: "agent-2", judge: 0.3, overall: 0.65 }),
      ],
      [version("1.0.0", true, { runs: 2, scored: 2 })],
      [
        version("1.0.0", true, { agent_id: "agent-1", median: 0.9, judge_gap: -0.2 }),
        version("1.0.0", true, { agent_id: "agent-2", median: 0.65, judge_gap: -0.7 }),
      ],
    );
    renderWithProviders(<ResultsByMission />);
    const chips = within(await screen.findByRole("group", { name: "Agents" })).getAllByRole("button");
    expect(chips.map((c) => c.getAttribute("aria-label"))).toEqual([
      "scout · median 90% · judge 20 below checks",
      "recon · median 65% · judge 70 below checks",
    ]);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("lights up one agent's runs from its chip: while hovered, and kept when pressed", async () => {
    serve(
      [
        scoreFixture({ run_id: "s", agent_id: "agent-1" }),
        scoreFixture({ run_id: "r", agent_id: "agent-2", judge: 0.3, overall: 0.65 }),
      ],
      [version("1.0.0", true, { runs: 2, scored: 2 })],
      [
        version("1.0.0", true, { agent_id: "agent-1", median: 0.9 }),
        version("1.0.0", true, { agent_id: "agent-2", median: 0.65 }),
      ],
    );
    renderWithProviders(<ResultsByMission />);
    const recon = await screen.findByRole("button", { name: /^recon/ });
    const dimmed = () =>
      screen
        .getAllByTestId("agreement-point")
        .map((p) => p.parentElement!.className.includes("opacity-20"));
    expect(dimmed()).toEqual([false, false]);
    fireEvent.mouseEnter(recon);
    expect(dimmed()).toEqual([true, false]);
    fireEvent.mouseLeave(recon);
    expect(dimmed()).toEqual([false, false]);
    fireEvent.click(recon);
    expect(recon).toHaveAttribute("aria-pressed", "true");
    expect(dimmed()).toEqual([true, false]);
    fireEvent.click(recon);
    expect(recon).toHaveAttribute("aria-pressed", "false");
    expect(dimmed()).toEqual([false, false]);
  });

  it("reveals whose run a point is on hover, and lights up that agent's other runs", async () => {
    serve(
      [
        scoreFixture({ run_id: "s", agent_id: "agent-1" }),
        scoreFixture({ run_id: "r1", agent_id: "agent-2", judge: 0.3, overall: 0.65 }),
        scoreFixture({ run_id: "r2", agent_id: "agent-2", judge: 0.4, overall: 0.7 }),
      ],
      [version("1.0.0", true, { runs: 3, scored: 3 })],
      [
        version("1.0.0", true, { agent_id: "agent-1", median: 0.9, judge_gap: -0.2 }),
        version("1.0.0", true, { agent_id: "agent-2", median: 0.68, judge_gap: -0.65 }),
      ],
    );
    renderWithProviders(<ResultsByMission />);
    await waitFor(() => expect(screen.getAllByTestId("agreement-point")).toHaveLength(3));
    const [, r1] = screen.getAllByTestId("agreement-point");
    expect(screen.queryByTestId("agreement-hover")).toBeNull();
    fireEvent.mouseEnter(r1);
    const label = screen.getByTestId("agreement-hover");
    expect(label).toHaveTextContent("recon · median 68% · judge 65 below checks");
    expect(label).toHaveTextContent("judge 30%");
    const dimmed = screen
      .getAllByTestId("agreement-point")
      .map((p) => p.parentElement!.className.includes("opacity-20"));
    expect(dimmed).toEqual([true, false, false]);
    fireEvent.mouseLeave(r1);
    expect(screen.queryByTestId("agreement-hover")).toBeNull();
  });

  it("warns when a version's runs were judged by more than one model", async () => {
    serve([scoreFixture()], [version("1.0.0", true, { judge_models: ["m-a", "m-b"] })]);
    renderWithProviders(<ResultsByMission />);
    expect(
      await screen.findByText("Judged by 2 models (m-a, m-b): their scores may not compare."),
    ).toBeInTheDocument();
  });

  it("describes each plot for assistive technology", async () => {
    serve([scoreFixture()], [version("1.0.0", true, { judge_gap: -0.2 })]);
    renderWithProviders(<ResultsByMission />);
    const plot = await screen.findByRole("img");
    expect(plot).toHaveAccessibleName("sqli-login v1.0.0: 1 scored run; judge 20 below checks on average.");
  });
});

describe("gapWords", () => {
  it.each([
    [null, "—"],
    [-0.05, "agree (within 5)"],
    [-0.42, "judge 42 below checks"],
    [0.12, "judge 12 above checks"],
  ])("%s → %s", (gap, words) => {
    expect(gapWords(gap)).toBe(words);
  });
});
