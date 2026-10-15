import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { DeleteAgentButton } from "./delete-agent-dialog";

function openAndConfirm() {
  fireEvent.click(screen.getByRole("button", { name: /delete agent/i }));
  fireEvent.click(screen.getByRole("button", { name: /delete permanently/i }));
}

describe("DeleteAgentButton", () => {
  it("explains, in the server's words, which run blocks the removal and why", async () => {
    // It used to print a bare "Delete failed." — and leave the rejected promise unhandled.
    server.use(
      http.delete("*/api/agents/alpha", () =>
        HttpResponse.json(
          {
            detail:
              "cannot remove agent 'alpha': run r-live is still running. Terminate it first.",
            reason: "running",
            run_ids: ["r-live"],
            forceable: false,
          },
          { status: 409 },
        ),
      ),
    );
    const onDeleted = vi.fn();
    renderWithProviders(<DeleteAgentButton name="alpha" onDeleted={onDeleted} />);
    openAndConfirm();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "run r-live is still running. Terminate it first.",
    );
    expect(screen.queryByRole("button", { name: /delete anyway/i })).toBeNull();
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("offers Delete anyway when a finished run's environment could not be torn down", async () => {
    const searches: string[] = [];
    server.use(
      http.delete("*/api/agents/alpha", ({ request }) => {
        const url = new URL(request.url);
        searches.push(url.search);
        if (url.searchParams.get("force") === "true")
          return new HttpResponse(null, { status: 204 });
        return HttpResponse.json(
          {
            detail: "cannot remove agent 'alpha': the environment of run r1 could not be torn down.",
            reason: "unreleased",
            run_ids: ["r1"],
            forceable: true,
          },
          { status: 409 },
        );
      }),
    );
    const onDeleted = vi.fn();
    renderWithProviders(<DeleteAgentButton name="alpha" onDeleted={onDeleted} />);
    openAndConfirm();
    expect(await screen.findByText(/could not be torn down/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /delete anyway/i }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
    expect(searches).toEqual(["", "?force=true"]);
  });

  it("forgets a refusal when closed, so reopening starts clean", async () => {
    server.use(
      http.delete("*/api/agents/alpha", () =>
        HttpResponse.json(
          {
            detail: "cannot remove agent 'alpha': the environment of run r1 could not be torn down.",
            reason: "unreleased",
            run_ids: ["r1"],
            forceable: true,
          },
          { status: 409 },
        ),
      ),
    );
    renderWithProviders(<DeleteAgentButton name="alpha" onDeleted={vi.fn()} />);
    openAndConfirm();
    expect(await screen.findByRole("button", { name: /delete anyway/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    fireEvent.click(screen.getByRole("button", { name: /delete agent/i }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: /delete permanently/i })).toBeInTheDocument();
  });
});
