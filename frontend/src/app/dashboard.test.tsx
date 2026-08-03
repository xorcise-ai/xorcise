import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture } from "@/test/fixtures";

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

import { DashboardView } from "@/features/dashboard/dashboard-view";

describe("Dashboard", () => {
  it("shows recent runs from the server", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "run-1", mission: "sqli-login" }),
          runFixture({ run_id: "run-2", mission: "idor-accounts" }),
        ]),
      ),
    );
    renderWithProviders(<DashboardView />);

    await waitFor(() =>
      expect(screen.getByText("idor-accounts")).toBeInTheDocument(),
    );
    expect(screen.getByText("sqli-login")).toBeInTheDocument();
  });

  it("is purely operational — the onboarding 'Start here' panel moved to Setup", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([runFixture({ run_id: "run-1" })]),
      ),
    );
    renderWithProviders(<DashboardView />);
    await waitFor(() => expect(screen.getByText("System")).toBeInTheDocument());

    // The first-run path now lives in the Setup page's readiness-aware step strip.
    expect(screen.queryByText(/Start here/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Register an agent/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Pick a mission/i)).not.toBeInTheDocument();
  });

  // The dashboard fills its pane with rows rather than leaving the lower half
  // dead: the recent-runs log takes the residual height and scrolls inside its
  // own card, so the title and the counts never move.
  it("gives the recent-runs log the residual height and its own scroller", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "run-1", mission: "sqli-login" }),
        ]),
      ),
    );
    const { container } = renderWithProviders(<DashboardView />);

    const list = await screen.findByRole("list");
    expect(list.className).toContain("overflow-y-auto");
    expect(list.className).toContain("flex-1");

    const root = container.firstElementChild!;
    expect(root.className).toContain("h-full");
    expect(root.className).not.toContain("overflow-y-auto");
  });

  it("shows an empty state when there are no runs", async () => {
    server.use(http.get("*/api/runs", () => HttpResponse.json([])));
    renderWithProviders(<DashboardView />);
    await waitFor(() =>
      expect(screen.getByText(/No runs yet/i)).toBeInTheDocument(),
    );
  });

  it("shows the overview counts and a system-health glance", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([{ id: "a", name: "scout", endpoint: null, otel: null, version: 1, created_at: "2026-06-29T10:00:00Z" }]),
      ),
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "r1", state: "active" }),
          runFixture({ run_id: "r2", state: "terminal", terminal_trigger: "done" }),
        ]),
      ),
    );
    renderWithProviders(<DashboardView />);
    // Overview stat tiles (labels + the system glance).
    expect(await screen.findByText("Agents")).toBeInTheDocument();
    expect(screen.getByText("Missions installed")).toBeInTheDocument();
    // §4: "Completed" relabelled to "Evaluated" (it counts every terminal run).
    expect(screen.getByText("Evaluated")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("System")).toBeInTheDocument());
  });
});
