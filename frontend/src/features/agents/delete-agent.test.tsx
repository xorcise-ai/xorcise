import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { DeleteAgentButton } from "./delete-agent-dialog";

describe("DeleteAgentButton", () => {
  it("warns about the cascade, then DELETEs and calls onDeleted", async () => {
    let deleted = false;
    server.use(
      http.delete("*/api/agents/scout", () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const onDeleted = vi.fn();
    renderWithProviders(<DeleteAgentButton name="scout" onDeleted={onDeleted} />);

    fireEvent.click(screen.getByRole("button", { name: "Delete agent" }));
    // confirm copy mentions the runs+results cascade
    expect(screen.getByText(/runs and results/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /delete permanently/i }));
    await waitFor(() => expect(deleted).toBe(true));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  });
});
