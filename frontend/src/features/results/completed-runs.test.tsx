import { describe, it, expect, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture, agentFixture } from "@/test/fixtures";

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

describe("CompletedRuns (agent-centric §14)", () => {
  it("summarises only agents with terminal runs", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([
          agentFixture({ id: "agent-1", name: "scout" }),
          agentFixture({ id: "agent-2", name: "raptor" }),
        ]),
      ),
      http.get("*/api/runs", () =>
        HttpResponse.json([
          // agent-1 has an active (non-terminal) run only → not summarised.
          runFixture({ run_id: "a", agent_id: "agent-1", state: "active" }),
          // agent-2 has a terminal run → summarised.
          runFixture({
            run_id: "b",
            agent_id: "agent-2",
            state: "terminal",
            terminal_trigger: "done",
          }),
        ]),
      ),
    );
    renderWithProviders(<CompletedRuns />);
    await waitFor(() =>
      expect(screen.getByText("raptor")).toBeInTheDocument(),
    );
    expect(screen.queryByText("scout")).not.toBeInTheDocument();
  });

  it("rolls the recorded score into the agent's Average and Best", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ id: "agent-1", name: "scout" })]),
      ),
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({
            run_id: "scored",
            agent_id: "agent-1",
            mission: "sqli-login",
            state: "terminal",
            terminal_trigger: "done",
          }),
        ]),
      ),
      http.get("*/api/runs/scored/result", () =>
        HttpResponse.json({
          grade: {
            run_id: "scored",
            overall: 0.9,
            breakdown: { deterministic: 1.0, judge: 0.8 },
            key_evidence: [],
            major_deductions: [],
            artifacts: [],
            hard_fails: [],
            judge_status: "ok",
            judge_breakdown: [],
            check_breakdown: [],
          },
          conditions: {},
          partial: false,
          partial_trigger: null,
        }),
      ),
    );
    renderWithProviders(<CompletedRuns />);
    // 90% appears for both Average and Best of the single scored run.
    await waitFor(() =>
      expect(screen.getAllByText("90%").length).toBeGreaterThanOrEqual(2),
    );
    const card = screen.getByText("scout").closest("a")!;
    // Card links to the agent's performance profile (§14).
    expect(card).toHaveAttribute(
      "href",
      "/agents/detail?name=scout",
    );
    // Runs count is shown.
    expect(within(card).getByText("Runs")).toBeInTheDocument();
  });

  it("shows a what/why/next empty state with Start Run when nothing is terminal", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([runFixture({ state: "active" })]),
      ),
    );
    renderWithProviders(<CompletedRuns />);
    await waitFor(() =>
      expect(screen.getByText(/No results yet/i)).toBeInTheDocument(),
    );
    const start = screen.getByRole("link", { name: /Start Run/i });
    expect(start).toHaveAttribute("href", "/runs/new");
  });
});
