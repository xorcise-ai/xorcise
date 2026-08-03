import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse, delay } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture, agentFixture, missionFixture } from "@/test/fixtures";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import Home from "./page";

function systemReady() {
  return HttpResponse.json({
    role: "all",
    planes: [{ name: "docker", ok: true, detail: "ok", location: "local daemon" }],
    db_schema: "head",
    catalog: { state: "connected", message: null, last_sync: null },
    remotes: [],
    home: "/h",
    db_url: "sqlite:///x",
    topology: "local",
  });
}

describe("Home first-run routing", () => {
  it("shows the welcome landing for a fresh install (no runs)", async () => {
    // Default handlers already return an empty runs list.
    renderWithProviders(<Home />);
    expect(await screen.findByText(/Get started with XORCISE/i)).toBeInTheDocument();
  });

  it("shows the dashboard once there are runs and setup is ready", async () => {
    server.use(
      http.get("*/api/system", () => systemReady()),
      http.get("*/api/agents", () => HttpResponse.json([agentFixture()])),
      http.get("*/api/missions", () =>
        HttpResponse.json([missionFixture({ installed: true })]),
      ),
      http.get("*/api/runs", () =>
        HttpResponse.json([runFixture({ mission: "idor-accounts" })]),
      ),
    );
    renderWithProviders(<Home />);
    // Dashboard-only marker: the RecentRuns heading (Welcome has no such section).
    expect(await screen.findByText("Recent runs")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText(/Get started with XORCISE/i)).not.toBeInTheDocument(),
    );
  });

  it("stays on Welcome (no dashboard jump) when runs exist but setup is not ready", async () => {
    // Prior runs exist, but default handlers leave setup incomplete (no docker
    // plane, no agent), so the Dashboard is unreachable. The router's loading
    // gate guarantees the runs query has resolved before Welcome mounts, so the
    // jump-CTA absence assertion is race-free — and would catch a runCount-only guard.
    server.use(http.get("*/api/runs", () => HttpResponse.json([runFixture()])));
    renderWithProviders(<Home />);
    expect(await screen.findByText(/Get started with XORCISE/i)).toBeInTheDocument();
    expect(screen.queryByText("Recent runs")).not.toBeInTheDocument();
    expect(screen.queryByText(/Go to dashboard/i)).not.toBeInTheDocument();
  });

  it("shows a neutral loading state before choosing a surface", async () => {
    server.use(
      http.get("*/api/runs", async () => {
        await delay(60);
        return HttpResponse.json([]);
      }),
    );
    renderWithProviders(<Home />);
    // Neither surface flashes; the router shows Loading until the queries resolve.
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText(/Get started with XORCISE/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Recent runs")).not.toBeInTheDocument();
    // Then it resolves (empty runs → Welcome).
    expect(await screen.findByText(/Get started with XORCISE/i)).toBeInTheDocument();
  });
});
