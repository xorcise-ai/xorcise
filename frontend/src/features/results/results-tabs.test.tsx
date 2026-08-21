import { describe, it, expect, vi } from "vitest";
import { screen, waitFor, within, fireEvent } from "@testing-library/react";
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

import { ResultsTabs } from "./results-tabs";

/** One registered agent with a scored terminal run + a second, still-active run. */
function seedRuns() {
  server.use(
    http.get("*/api/agents", () =>
      HttpResponse.json([agentFixture({ id: "agent-1", name: "scout" })]),
    ),
    http.get("*/api/runs", () =>
      HttpResponse.json([
        runFixture({
          run_id: "done-1",
          agent_id: "agent-1",
          mission: "sqli-login",
          state: "terminal",
          terminal_trigger: "done",
        }),
        runFixture({
          run_id: "live-1",
          agent_id: "agent-1",
          mission: "xss-stored",
          state: "active",
        }),
      ]),
    ),
    http.get("*/api/runs/done-1/result", () =>
      HttpResponse.json({
        grade: {
          run_id: "done-1",
          overall: 0.9,
          breakdown: {},
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
}

describe("ResultsTabs (secondary views §14)", () => {
  it("defaults to the Agents view and exposes all three tabs", async () => {
    seedRuns();
    renderWithProviders(<ResultsTabs />);

    // Three view tabs, Agents selected by default.
    expect(screen.getByRole("tab", { name: "Agents" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "Missions" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "All Runs" })).toBeInTheDocument();

    // Agent-centric card is what shows first.
    await waitFor(() =>
      expect(screen.getByText("scout")).toBeInTheDocument(),
    );
  });

  it("Missions view groups the same terminal runs by mission", async () => {
    seedRuns();
    renderWithProviders(<ResultsTabs />);

    fireEvent.click(screen.getByRole("tab", { name: "Missions" }));

    // The completed mission appears with its performance tiles; the active-only
    // mission (xss-stored) has no terminal run so it is not summarised.
    await waitFor(() =>
      expect(screen.getByText("sqli-login")).toBeInTheDocument(),
    );
    const panel = screen.getByRole("tabpanel");
    expect(within(panel).getByText("Runs")).toBeInTheDocument();
    expect(within(panel).queryByText("xss-stored")).not.toBeInTheDocument();
    // The scored run's 90% is surfaced (Average + Best).
    await waitFor(() =>
      expect(within(panel).getAllByText("90%").length).toBeGreaterThanOrEqual(
        2,
      ),
    );
  });

  it("All Runs view lists every run, active and terminal", async () => {
    seedRuns();
    renderWithProviders(<ResultsTabs />);

    fireEvent.click(screen.getByRole("tab", { name: "All Runs" }));

    const panel = screen.getByRole("tabpanel");
    // The score ledger renders each run in both the ≥sm table and the narrow stacked list
    // (responsive dual-render), so a mission legitimately appears more than once.
    await waitFor(() =>
      expect(within(panel).getAllByText("sqli-login").length).toBeGreaterThan(0),
    );
    // The still-active run is present here even though it never rolled into a summary.
    expect(within(panel).getAllByText("xss-stored").length).toBeGreaterThan(0);
  });

  it("shows a what/why/next empty state with Start run when there are no runs", async () => {
    server.use(http.get("*/api/runs", () => HttpResponse.json([])));
    renderWithProviders(<ResultsTabs />);

    // Agents (default) empty state.
    await waitFor(() =>
      expect(screen.getByText(/No results yet/i)).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("link", { name: /Start run/i }),
    ).toHaveAttribute("href", "/runs/new");

    // All Runs empty state also answers what/why/next.
    fireEvent.click(screen.getByRole("tab", { name: "All Runs" }));
    await waitFor(() =>
      expect(screen.getByText(/No runs yet/i)).toBeInTheDocument(),
    );
  });
});
