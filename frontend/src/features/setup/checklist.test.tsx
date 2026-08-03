import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { agentFixture, missionFixture } from "@/test/fixtures";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { ReadinessChecklist } from "./checklist";

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

describe("ReadinessChecklist", () => {
  it("shows a neutral 'checking' state during load, not 'todo' warnings", () => {
    server.use(
      http.get("*/api/system", () => systemReady()),
      http.get("*/api/agents", () => HttpResponse.json([])),
      http.get("*/api/missions", () => HttpResponse.json([])),
    );
    renderWithProviders(<ReadinessChecklist />);
    // First paint: the status queries are still pending — items read as checking.
    expect(screen.getAllByText(/checking/i).length).toBeGreaterThan(0);
  });

  it("shows 'ready' when all required items pass", async () => {
    server.use(
      http.get("*/api/system", () => systemReady()),
      http.get("*/api/agents", () => HttpResponse.json([agentFixture({ name: "scout" })])),
      http.get("*/api/missions", () =>
        HttpResponse.json([missionFixture({ installed: true })]),
      ),
    );
    renderWithProviders(<ReadinessChecklist />);
    await waitFor(() =>
      expect(screen.getByText(/You’re ready to run/i)).toBeInTheDocument(),
    );
    expect(screen.getByText("An agent is registered")).toBeInTheDocument();
    expect(screen.getByText("Docker available")).toBeInTheDocument();
  });

  it("collapses to a banner with Start a Run + a diagnostic expander when ready", async () => {
    server.use(
      http.get("*/api/system", () => systemReady()),
      http.get("*/api/agents", () => HttpResponse.json([agentFixture({ name: "scout" })])),
      http.get("*/api/missions", () =>
        HttpResponse.json([missionFixture({ installed: true })]),
      ),
    );
    renderWithProviders(<ReadinessChecklist startHref="/runs/new" />);
    const cta = await screen.findByText("Start a Run");
    // The next action is emphasised as a real link to the state-aware target (§5).
    expect(cta.closest("a")).toHaveAttribute("href", "/runs/new");
    // The full list is retained but tucked behind a diagnostic expander (§5).
    expect(screen.getByText("Diagnostic checklist")).toBeInTheDocument();
    expect(screen.getByText("An agent is registered")).toBeInTheDocument();
  });

  it("flags missing agent + mission as required to-dos", async () => {
    server.use(
      http.get("*/api/system", () => systemReady()),
      http.get("*/api/agents", () => HttpResponse.json([])),
      http.get("*/api/missions", () => HttpResponse.json([])),
      // catalog disconnected so 'mission available' is not satisfied
      http.get("*/api/system", () =>
        HttpResponse.json({
          role: "all",
          planes: [{ name: "docker", ok: true, detail: "ok", location: "local daemon" }],
          db_schema: "head",
          catalog: { state: "disconnected", message: null, last_sync: null },
          remotes: [],
          home: "/h",
          db_url: "sqlite:///x",
          topology: "local",
        }),
      ),
    );
    renderWithProviders(<ReadinessChecklist />);
    await waitFor(() =>
      expect(screen.getByText("A mission is available")).toBeInTheDocument(),
    );
    expect(screen.queryByText(/You’re ready to run/i)).not.toBeInTheDocument();
    // Recommended section still lists the judge.
    expect(screen.getByText("Judge model configured")).toBeInTheDocument();
  });
});
