import { describe, it, expect } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { RunLive } from "./run-live";

function run(state: string, trigger: string | null = null) {
  return {
    run_id: "r1",
    agent_id: "a1",
    mission: "idor-accounts",
    state,
    created_at: "2026-06-29T05:00:00Z",
    budget_seconds: 600,
    terminal_trigger: trigger,
    completed_at: trigger ? "2026-06-29T05:01:00Z" : null,
  };
}

describe("RunLive terminate", () => {
  it("shows Terminate for an active run and POSTs on click", async () => {
    let terminated = false;
    server.use(
      http.get("*/api/runs", () => HttpResponse.json([run("active")])),
      http.post("*/api/runs/r1/terminate", () => {
        terminated = true;
        return HttpResponse.json(run("terminal", "operator"));
      }),
    );
    renderWithProviders(<RunLive runId="r1" />);
    fireEvent.click(await screen.findByRole("button", { name: /terminate/i }));
    await waitFor(() => expect(terminated).toBe(true));
  });

  it("reacts to the async terminal flip after Terminate without a refresh", async () => {
    let terminated = false;
    let getsAfterTerminate = 0;
    server.use(
      http.get("*/api/runs", () => {
        if (terminated) getsAfterTerminate += 1;
        // Termination is async: the immediate post-terminate refetch still sees
        // "active"; only a later poll observes the terminal flip.
        const done = terminated && getsAfterTerminate >= 2;
        return HttpResponse.json([done ? run("terminal", "operator") : run("active")]);
      }),
      http.post("*/api/runs/r1/terminate", () => {
        terminated = true;
        return HttpResponse.json(run("terminal", "operator"));
      }),
    );
    renderWithProviders(<RunLive runId="r1" />);
    fireEvent.click(await screen.findByRole("button", { name: /terminate/i }));
    await waitFor(
      () => expect(screen.queryByRole("button", { name: /terminate/i })).toBeNull(),
      { timeout: 5000 },
    );
    expect(screen.getByText(/View result/i)).toBeInTheDocument();
  });

  it("hides Terminate for a terminal run", async () => {
    server.use(http.get("*/api/runs", () => HttpResponse.json([run("terminal", "done")])));
    renderWithProviders(<RunLive runId="r1" />);
    await screen.findByText(/idor-accounts/i);
    expect(screen.queryByRole("button", { name: /terminate/i })).toBeNull();
  });
});
