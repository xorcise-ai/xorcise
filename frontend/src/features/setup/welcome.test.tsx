import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture, agentFixture, missionFixture } from "@/test/fixtures";
import { Welcome } from "./welcome";

/** A fully-ready system: docker up, schema current, catalog connected. */
function mockReady() {
  server.use(
    http.get("*/api/system", () =>
      HttpResponse.json({
        role: "all",
        planes: [{ name: "docker", ok: true, detail: "ok", location: "local daemon" }],
        db_schema: "head",
        catalog: { state: "connected", message: null, last_sync: null },
        remotes: [],
        home: "/h",
        db_url: "sqlite:///x",
        topology: "local",
      }),
    ),
    http.get("*/api/agents", () => HttpResponse.json([agentFixture()])),
    http.get("*/api/missions", () =>
      HttpResponse.json([missionFixture({ installed: true })]),
    ),
  );
}

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

describe("Welcome", () => {
  it("shows the get-started hero, backend status, and quick start", async () => {
    renderWithProviders(<Welcome />);
    expect(await screen.findByText(/Get started with XORCISE/i)).toBeInTheDocument();
    expect(await screen.findByText(/Backend running/i)).toBeInTheDocument();
    expect(screen.getByText("Start a Run")).toBeInTheDocument();
    expect(screen.getByText("Read Documentation")).toBeInTheDocument();
  });

  it("describes the product harness-agnostically — no single-vendor wording", async () => {
    // XORCISE evaluates ANY cyber AI agent (OpenHands, Claude Code, …); the hero must
    // not claim it tests one specific harness. Regression for the welcome-banner drift.
    renderWithProviders(<Welcome />);
    await screen.findByText(/Get started with XORCISE/i);
    expect(screen.queryByText(/Claude Code/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Let Claude prove/i)).not.toBeInTheDocument();
    expect(screen.getByText(/cyber AI agents/i)).toBeInTheDocument();
  });

  it("shows the compact four-step how-it-works strip (§5 labels)", async () => {
    renderWithProviders(<Welcome />);
    expect(await screen.findByText("Register an agent")).toBeInTheDocument();
    expect(screen.getByText("Install a mission")).toBeInTheDocument();
    expect(screen.getByText("Review the result")).toBeInTheDocument();
  });

  it("makes every step a link to the page that performs it (the start path)", async () => {
    // The Dashboard's separate "Start here" panel is gone — its navigation value
    // lives here, inside the strip that already knows what's done and what's next.
    renderWithProviders(<Welcome />);
    const hrefFor = async (label: string) =>
      (await screen.findByText(label)).closest("a")?.getAttribute("href");

    expect(await hrefFor("Register an agent")).toBe("/agents");
    expect(await hrefFor("Install a mission")).toBe("/missions");
    expect(await hrefFor("Start a run")).toBe("/runs/new");
    expect(await hrefFor("Review the result")).toBe("/results");
  });

  it("embeds the readiness checklist", async () => {
    renderWithProviders(<Welcome />);
    expect(
      await screen.findByText("An agent is registered"),
    ).toBeInTheDocument();
  });

  it("offers a jump to the dashboard when runs exist and setup is ready", async () => {
    mockReady();
    server.use(http.get("*/api/runs", () => HttpResponse.json([runFixture()])));
    renderWithProviders(<Welcome />);
    const link = await screen.findByText(/Go to dashboard/i);
    expect(link.closest("a")).toHaveAttribute("href", "/");
  });

  it("hides the dashboard jump when there are no runs", async () => {
    mockReady();
    renderWithProviders(<Welcome />);
    // Wait for first paint, then assert the link is absent (runs default empty).
    await screen.findByText(/Get started with XORCISE/i);
    await waitFor(() =>
      expect(screen.queryByText(/Go to dashboard/i)).not.toBeInTheDocument(),
    );
  });
  // The has-runs-but-not-ready guard is tested at the router level
  // (home-routing.test.tsx), where the loading gate guarantees the runs query
  // has resolved before Welcome mounts — making the absence assertion race-free.
});
