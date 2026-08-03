import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture, agentFixture } from "@/test/fixtures";
import { useToastStore } from "@/stores/toasts";
import { useRuns } from "./queries";

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

import { RunCard } from "./run-card";

const terminalRun = (over: Parameters<typeof runFixture>[0] = {}) =>
  runFixture({
    state: "terminal",
    terminal_trigger: "done",
    completed_at: "2026-06-29T10:10:00Z",
    ...over,
  });

describe("RunCard", () => {
  it("shows the agent name, not the raw agent id", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([
          agentFixture({ id: "1c9343952bf64ac6b92785daeda54e1c", name: "scout" }),
        ]),
      ),
    );
    renderWithProviders(
      <RunCard
        run={runFixture({
          run_id: "run-1",
          agent_id: "1c9343952bf64ac6b92785daeda54e1c",
        })}
      />,
    );
    expect(await screen.findByText(/scout/)).toBeInTheDocument();
    // the raw agent id must not appear on the card
    expect(
      screen.queryByText(/1c9343952bf64ac6b92785daeda54e1c/),
    ).toBeNull();
  });
});

describe("RunCard delete affordance", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [] });
  });

  it("shows the delete icon on a terminal run", () => {
    renderWithProviders(<RunCard run={terminalRun()} />);
    expect(
      screen.getByRole("button", { name: /delete run/i }),
    ).toBeInTheDocument();
  });

  it("hides the delete icon on a non-terminal run", () => {
    renderWithProviders(<RunCard run={runFixture()} />);
    expect(screen.queryByRole("button", { name: /delete run/i })).toBeNull();
  });

  it("confirms inside the card, outside the card link (no navigation)", () => {
    const run = terminalRun({ name: "sqli-login · scout #1" });
    renderWithProviders(<RunCard run={run} />);
    const btn = screen.getByRole("button", { name: /delete run/i });
    // Structural guarantee: the affordance is a sibling of the <Link>, not inside it,
    // so clicking it can never trigger the card's navigation.
    expect(btn.closest("a")).toBeNull();

    fireEvent.click(btn);
    // The confirmation is an in-card alertdialog naming the run it will destroy — the
    // operator should never have to match a floating modal to a card by memory.
    const prompt = screen.getByRole("alertdialog");
    expect(prompt).toHaveAccessibleName(`Delete run ${run.name}?`);
    expect(prompt.closest("a")).toBeNull();
  });

  it("takes the card out of the tab order while confirming", () => {
    renderWithProviders(<RunCard run={terminalRun()} />);
    fireEvent.click(screen.getByRole("button", { name: /delete run/i }));
    // The card sits directly behind the prompt; leaving it focusable/announced would offer
    // the very record the operator is being asked to destroy as a live link.
    const link = document.querySelector("a[aria-hidden='true']");
    expect(link).not.toBeNull();
    expect(link).toHaveAttribute("tabindex", "-1");
  });

  it("Escape backs out of the confirmation without deleting", async () => {
    let deleted = false;
    server.use(
      http.delete("*/api/runs/*", () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<RunCard run={terminalRun()} />);
    fireEvent.click(screen.getByRole("button", { name: /delete run/i }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(deleted).toBe(false);
  });

  it("confirm DELETEs the run, toasts, and the card leaves the list", async () => {
    let runs = [terminalRun({ run_id: "run-1", mission: "sqli-login" })];
    let deleted = false;
    server.use(
      http.get("*/api/runs", () => HttpResponse.json(runs)),
      http.delete("*/api/runs/run-1", () => {
        deleted = true;
        runs = [];
        return new HttpResponse(null, { status: 204 });
      }),
    );

    // Minimal list: cards driven by the shared ["runs"] query, so the
    // useDeleteRun invalidation is what removes the card.
    function ListHarness() {
      const q = useRuns();
      return (
        <>{q.data?.map((r) => <RunCard key={r.run_id} run={r} />) ?? null}</>
      );
    }
    renderWithProviders(<ListHarness />);

    fireEvent.click(
      await screen.findByRole("button", { name: /delete run/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => expect(deleted).toBe(true));
    await waitFor(() =>
      expect(screen.queryByText("sqli-login")).toBeNull(),
    );
    expect(
      useToastStore.getState().toasts.some((t) => t.title === "Run deleted"),
    ).toBe(true);
  });
});
