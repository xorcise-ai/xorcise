import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture, agentFixture, missionFixture } from "@/test/fixtures";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
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

import { NewRunForm } from "./new-run-form";

/**
 * /runs/new is the ONE create-run experience: the agent surfaces used to open a
 * cut-down modal with the agent fixed, so the intent of that flow (agent locked
 * in, mission + budget chosen, run posted, jump to the live view) is asserted
 * here against the preselected full page instead.
 */
describe("NewRunForm preselection", () => {
  function mockCatalog() {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([
          agentFixture({ name: "scout", kind: "claude-code" }),
          agentFixture({ id: "agent-2", name: "other" }),
        ]),
      ),
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({ mission_id: "sqli-login", name: "SQLi Login Bypass" }),
        ]),
      ),
    );
  }

  it("preselects the agent handed over in the query string", async () => {
    mockCatalog();
    renderWithProviders(<NewRunForm initialAgent="scout" />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "scout" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    // The roster row confirms the handover — name + harness, straight from the list.
    expect(screen.getByText("Claude Code CLI")).toBeInTheDocument();
    // Review card echoes it as the run's agent.
    expect(screen.getAllByText("scout").length).toBeGreaterThan(0);
  });

  it("preselects agent AND mission together, then posts that pair", async () => {
    let body: Record<string, unknown> | null = null;
    mockCatalog();
    server.use(
      http.post("*/api/runs", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { ...runFixture({ run_id: "run-42" }), run_control_key: "k" },
          { status: 201 },
        );
      }),
    );
    push.mockClear();

    renderWithProviders(
      <NewRunForm initialAgent="scout" initialMission="sqli-login" />,
    );

    // Both selections are already made — the operator only has to start.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "scout" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    // Twice: the picker row plus the Review card echoing it back as the run's mission.
    await waitFor(() =>
      expect(screen.getAllByText("SQLi Login Bypass").length).toBeGreaterThan(1),
    );

    fireEvent.click(screen.getByRole("button", { name: /Start run/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({
      agent: "scout",
      mission: "sqli-login",
      budget_seconds: 600,
    });
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/runs/live?id=run-42"),
    );
  });

  it("sends the budget chosen on the slider (minutes → seconds)", async () => {
    let body: Record<string, unknown> | null = null;
    mockCatalog();
    server.use(
      http.post("*/api/runs", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { ...runFixture({ run_id: "run-9" }), run_control_key: "k" },
          { status: 201 },
        );
      }),
    );

    renderWithProviders(
      <NewRunForm initialAgent="scout" initialMission="sqli-login" />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "scout" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );

    fireEvent.change(screen.getByRole("slider", { name: /budget/i }), {
      target: { value: "15" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Start run/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ budget_seconds: 900 });
  });

  it("starts empty when no preselection is handed over", async () => {
    mockCatalog();
    renderWithProviders(<NewRunForm />);
    const row = await screen.findByRole("button", { name: "scout" });
    expect(row).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: /Start run/i })).toBeDisabled();
  });
});
