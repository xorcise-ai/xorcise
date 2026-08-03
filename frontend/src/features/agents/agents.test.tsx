import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { agentFixture, runFixture, missionFixture } from "@/test/fixtures";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));
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

import { AgentList } from "./agent-list";
import { AgentDetail } from "./agent-detail";

beforeEach(() => push.mockClear());

describe("AgentList", () => {
  it("navigates to /agents/new on Register agent", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
    );
    renderWithProviders(<AgentList />);
    await waitFor(() => expect(screen.getByText("scout")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Register agent/i }));
    expect(push).toHaveBeenCalledWith("/agents/new");
  });

  it("shows an empty state when there are no agents", async () => {
    server.use(http.get("*/api/agents", () => HttpResponse.json([])));
    renderWithProviders(<AgentList />);
    await waitFor(() =>
      expect(screen.getByText(/No agents yet/i)).toBeInTheDocument(),
    );
  });

  it("shows harness identity + a frontend-aggregated performance summary and card actions", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([
          agentFixture({ name: "scout", id: "agent-1", kind: "codex", model: "gpt-5" }),
        ]),
      ),
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({
            run_id: "run-1",
            agent_id: "agent-1",
            state: "terminal",
            terminal_trigger: "done",
            completed_at: "2026-06-29T10:10:00Z",
          }),
        ]),
      ),
      http.get("*/api/runs/run-1/result", () =>
        HttpResponse.json({ partial: false, grade: { overall: 0.5 } }),
      ),
    );
    renderWithProviders(<AgentList />);
    await waitFor(() => expect(screen.getByText("scout")).toBeInTheDocument());

    // Harness identity + model config surfaced (report §7). The display name names the
    // artefact under evaluation — the CLI — while the `codex` kind slug stays contractual.
    // Scoped to the card: the filter facets list the same labels as options.
    const card = screen.getByText("scout").closest("li")!;
    expect(within(card).getByText(/Codex CLI/)).toBeInTheDocument();
    expect(within(card).getByText(/gpt-5/)).toBeInTheDocument();

    // Frontend-aggregated performance summary (Average/Best 50% from the one scored run).
    await waitFor(() =>
      expect(screen.getAllByText("50%").length).toBeGreaterThan(0),
    );

    // Card actions: Start Run + View Agent, both links. Start Run deep-links into the one
    // create-run flow with this agent preselected — there is no per-agent modal any more.
    const start = screen.getByRole("link", { name: /Start Run/i });
    expect(start).toHaveAttribute("href", "/runs/new?agent=scout");
    expect(
      screen.queryByRole("button", { name: /Start Run/i }),
    ).not.toBeInTheDocument();
    const view = screen.getByRole("link", { name: /View Agent/i });
    expect(view).toHaveAttribute("href", "/agents/detail?name=scout");
  });

  it("url-encodes the agent name in the Start Run link", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "red team/1" })]),
      ),
    );
    renderWithProviders(<AgentList />);
    const start = await screen.findByRole("link", { name: /Start Run/i });
    expect(start).toHaveAttribute("href", "/runs/new?agent=red%20team%2F1");
  });

  it("shows a per-card 'no evaluations yet' state for an agent with no runs", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
      http.get("*/api/runs", () => HttpResponse.json([])),
    );
    renderWithProviders(<AgentList />);
    await waitFor(() =>
      expect(screen.getByText(/No evaluations yet/i)).toBeInTheDocument(),
    );
  });

  it("lists agents newest → oldest regardless of transport order", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([
          agentFixture({ id: "a-old", name: "probe", created_at: "2026-05-01T10:00:00Z" }),
          agentFixture({ id: "a-new", name: "raider", created_at: "2026-07-10T10:00:00Z" }),
          agentFixture({ id: "a-mid", name: "scout", created_at: "2026-06-15T10:00:00Z" }),
        ]),
      ),
    );
    renderWithProviders(<AgentList />);
    await screen.findByText("raider");

    const names = screen
      .getAllByText(/^(probe|scout|raider)$/)
      .map((el) => el.textContent);
    expect(names).toEqual(["raider", "scout", "probe"]);
  });

  it("filters agents by harness", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([
          agentFixture({ id: "a1", name: "probe", kind: "codex", created_at: "2026-05-01T10:00:00Z" }),
          agentFixture({ id: "a2", name: "scout", kind: "claude-code", created_at: "2026-06-01T10:00:00Z" }),
          agentFixture({ id: "a3", name: "raider", kind: "codex", created_at: "2026-07-01T10:00:00Z" }),
        ]),
      ),
    );
    renderWithProviders(<AgentList />);
    await screen.findByText("probe");

    fireEvent.change(screen.getByLabelText("Harness"), {
      target: { value: "Codex CLI" },
    });

    expect(screen.getByText("probe")).toBeInTheDocument();
    expect(screen.getByText("raider")).toBeInTheDocument();
    expect(screen.queryByText("scout")).not.toBeInTheDocument();

    // Clear filters restores the full list.
    fireEvent.click(screen.getByRole("button", { name: /clear filters/i }));
    expect(screen.getByText("scout")).toBeInTheDocument();
  });

  it("filters agents by model, including the Not disclosed bucket", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([
          agentFixture({ id: "a1", name: "probe", model: "gpt-5.6", created_at: "2026-05-01T10:00:00Z" }),
          agentFixture({ id: "a2", name: "scout", created_at: "2026-06-01T10:00:00Z" }),
          agentFixture({ id: "a3", name: "raider", model: "gpt-5.6", created_at: "2026-07-01T10:00:00Z" }),
        ]),
      ),
    );
    renderWithProviders(<AgentList />);
    await screen.findByText("probe");

    const modelFacet = screen.getByLabelText("Model");
    // The undisclosed bucket sorts after the real models.
    expect(
      within(modelFacet)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["All models", "gpt-5.6", "Not disclosed"]);

    fireEvent.change(modelFacet, { target: { value: "gpt-5.6" } });
    expect(screen.getByText("probe")).toBeInTheDocument();
    expect(screen.getByText("raider")).toBeInTheDocument();
    expect(screen.queryByText("scout")).not.toBeInTheDocument();

    fireEvent.change(modelFacet, { target: { value: "Not disclosed" } });
    expect(screen.getByText("scout")).toBeInTheDocument();
    expect(screen.queryByText("probe")).not.toBeInTheDocument();
  });

  it("shows a no-match state when combined filters exclude every agent", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([
          agentFixture({ id: "a1", name: "probe", kind: "codex", model: "gpt-5.6" }),
          agentFixture({ id: "a2", name: "scout", kind: "claude-code", model: "claude-opus-4-8" }),
        ]),
      ),
    );
    renderWithProviders(<AgentList />);
    await screen.findByText("probe");

    fireEvent.change(screen.getByLabelText("Harness"), {
      target: { value: "Codex CLI" },
    });
    fireEvent.change(screen.getByLabelText("Model"), {
      target: { value: "claude-opus-4-8" },
    });

    expect(screen.getByText(/no agents match/i)).toBeInTheDocument();
    expect(screen.queryByText("probe")).not.toBeInTheDocument();
  });
});

describe("AgentDetail", () => {
  it("renders a not-found state for a missing agent", async () => {
    server.use(http.get("*/api/agents", () => HttpResponse.json([])));
    renderWithProviders(<AgentDetail name="ghost" />);
    await waitFor(() =>
      expect(screen.getByText("Agent not found")).toBeInTheDocument(),
    );
  });

  it("renders the agent header meta and a compact, enriched run-history table", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([
          agentFixture({ name: "scout", version: 3, model: "claude-opus-4-1" }),
        ]),
      ),
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({
            run_id: "run-9",
            agent_id: "agent-1",
            mission: "sqli-login",
            state: "terminal",
            terminal_trigger: "budget",
          }),
        ]),
      ),
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({ mission_id: "sqli-login", name: "SQLi Login Bypass" }),
        ]),
      ),
      http.get("*/api/agents/scout/history", () =>
        HttpResponse.json([
          {
            agent_id: "agent-1",
            run_id: "run-9",
            overall: 0.8,
            deterministic: 0.9,
            judge: 0.7,
            partial: true,
            partial_trigger: "budget",
            conditions: {
              agent_version: 3,
              mission_version: 2,
              budget_seconds: 600,
              model: "claude-opus-4-1",
            },
            trace_ref: null,
            created_at: "2026-06-29T10:00:00Z",
          },
        ]),
      ),
    );
    renderWithProviders(<AgentDetail name="scout" />);
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "scout" }),
      ).toBeInTheDocument(),
    );
    // header meta: version badge + disclosed model
    expect(screen.getByText("v3")).toBeInTheDocument();
    expect(screen.getAllByText("claude-opus-4-1").length).toBeGreaterThan(0);
    // history row: the mission name links through to the run result (view-result capability kept).
    const links = await screen.findAllByRole("link", {
      name: /SQLi Login Bypass/i,
    });
    expect(links[0]).toHaveAttribute("href", "/runs/result?id=run-9");
    // Status badge from the shared §10 vocabulary (budget trigger → "Partial").
    expect(screen.getAllByText("Partial").length).toBeGreaterThan(0);
    // Scores are shown; the raw run id is no longer rendered as a label.
    expect(screen.getAllByText("80%").length).toBeGreaterThan(0);
    expect(screen.queryByText("run-9")).toBeNull();
  });

  it("starts a run through the shared /runs/new flow, not a modal", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
      http.get("*/api/agents/scout/history", () => HttpResponse.json([])),
    );
    renderWithProviders(<AgentDetail name="scout" />);
    const start = await screen.findByRole("link", { name: /Start Run/i });
    expect(start).toHaveAttribute("href", "/runs/new?agent=scout");
    expect(
      screen.queryByRole("button", { name: /Start Run/i }),
    ).not.toBeInTheDocument();
  });

  it("shows a clean empty state when the agent has no runs", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
      http.get("*/api/agents/scout/history", () => HttpResponse.json([])),
    );
    renderWithProviders(<AgentDetail name="scout" />);
    await waitFor(() =>
      expect(screen.getByText(/No runs yet for this agent/i)).toBeInTheDocument(),
    );
  });

  it("navigates to /agents/new?agent=<name> on Edit and keeps Delete", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
      http.get("*/api/agents/scout/history", () => HttpResponse.json([])),
    );
    renderWithProviders(<AgentDetail name="scout" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "scout" })).toBeInTheDocument(),
    );
    // Delete agent button still present (test-asserted selector)
    expect(
      screen.getByRole("button", { name: "Delete agent" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Edit$/i }));
    expect(push).toHaveBeenCalledWith("/agents/new?agent=scout");
  });

  it("url-encodes the agent name in the Edit navigation", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "red team/1" })]),
      ),
      http.get("*/api/agents/red%20team%2F1/history", () =>
        HttpResponse.json([]),
      ),
    );
    renderWithProviders(<AgentDetail name="red team/1" />);
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "red team/1" }),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("button", { name: /^Edit$/i }));
    expect(push).toHaveBeenCalledWith("/agents/new?agent=red%20team%2F1");
  });
});
