import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { IngestButton } from "./ingest-button";

describe("IngestButton", () => {
  it("opens a coming-soon preview and can return to the catalog", async () => {
    renderWithProviders(<IngestButton />);
    const button = screen.getByRole("button", { name: /ingest a bundle/i });
    expect(button).toBeEnabled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(button);

    expect(screen.getByRole("dialog", { name: /bundle ingestion/i })).toBeInTheDocument();
    expect(screen.getByText(/bring your own missions/i)).toBeInTheDocument();
    expect(screen.getByText(/coming soon/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /back to catalog/i }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("never posts an ingest while the preview is shown", () => {
    const posted = vi.fn();
    server.use(
      http.post("*/api/missions/ingest", () => {
        posted();
        return HttpResponse.json({ job_id: "job-1" }, { status: 202 });
      }),
    );

    renderWithProviders(<IngestButton />);
    fireEvent.click(screen.getByRole("button", { name: /ingest a bundle/i }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /use this folder/i })).not.toBeInTheDocument();
    expect(posted).not.toHaveBeenCalled();
  });
});
