import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { DeleteRunButton, DeleteRunDialog } from "./delete-run-dialog";

describe("DeleteRunDialog", () => {
  it("surfaces the server's refusal inline, verbatim, and stays open", async () => {
    // The dialog used to print its own "This run is still active — terminate it before deleting"
    // for any 409. That was wrong for a `created` run (nothing is active) and threw away the state
    // the server had just named. Show what the server said.
    server.use(
      http.delete("*/api/runs/r2", () =>
        HttpResponse.json(
          { detail: "run 'r2' has not finished (state: created) — terminate it before deleting" },
          { status: 409 },
        ),
      ),
    );
    const onClose = vi.fn();
    const onDeleted = vi.fn();
    renderWithProviders(
      <DeleteRunDialog runId="r2" open onClose={onClose} onDeleted={onDeleted} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /delete permanently/i }));
    expect(await screen.findByText(/state: created/i)).toBeInTheDocument();
    expect(screen.queryByText(/still active/i)).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("offers Delete anyway only when the refusal is a failed teardown, and forces it", async () => {
    // #173: a finished run whose environment could not be torn down is the one refusal the
    // operator may override, accepting that its container or tailnet nodes may stay up.
    const urls: string[] = [];
    server.use(
      http.delete("*/api/runs/r4", ({ request }) => {
        urls.push(request.url);
        if (new URL(request.url).searchParams.get("force") === "true")
          return new HttpResponse(null, { status: 204 });
        return HttpResponse.json(
          {
            detail: "the environment of run r4 could not be torn down (tailnet: down).",
            reason: "unreleased",
            run_ids: ["r4"],
            forceable: true,
          },
          { status: 409 },
        );
      }),
    );
    const onDeleted = vi.fn();
    renderWithProviders(
      <DeleteRunDialog runId="r4" open onClose={vi.fn()} onDeleted={onDeleted} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /delete permanently/i }));
    expect(await screen.findByText(/could not be torn down/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /delete anyway/i }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
    expect(urls.map((u) => new URL(u).search)).toEqual(["", "?force=true"]);
  });

  it("does not offer Delete anyway for a refusal forcing cannot fix", async () => {
    server.use(
      http.delete("*/api/runs/r5", () =>
        HttpResponse.json(
          {
            detail: "run r5 is still being graded.",
            reason: "grading",
            run_ids: ["r5"],
            forceable: false,
          },
          { status: 409 },
        ),
      ),
    );
    renderWithProviders(
      <DeleteRunDialog runId="r5" open onClose={vi.fn()} onDeleted={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /delete permanently/i }));
    expect(await screen.findByText(/still being graded/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete anyway/i })).toBeNull();
  });

  it("falls back to a generic line when the server gives no detail", async () => {
    server.use(
      http.delete("*/api/runs/r3", () => new HttpResponse(null, { status: 500 })),
    );
    renderWithProviders(
      <DeleteRunDialog runId="r3" open onClose={vi.fn()} onDeleted={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /delete permanently/i }));
    expect(await screen.findByText(/delete failed/i)).toBeInTheDocument();
  });
});

describe("DeleteRunButton", () => {
  it("confirms, then DELETEs the run and calls onDeleted", async () => {
    let deleted = false;
    server.use(
      http.delete("*/api/runs/r1", () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const onDeleted = vi.fn();
    renderWithProviders(<DeleteRunButton runId="r1" onDeleted={onDeleted} />);

    fireEvent.click(screen.getByRole("button", { name: /delete run/i }));
    expect(screen.getByText(/can.?t be undone/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /delete permanently/i }));
    await waitFor(() => expect(deleted).toBe(true));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  });
});
