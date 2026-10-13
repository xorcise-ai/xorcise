import { describe, it, expect } from "vitest";
import { screen, fireEvent, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { PruneImagesPanel } from "./prune-images";
import type { PruneReport } from "@/lib/api/types";

const CANDIDATES: PruneReport["candidates"] = [
  {
    image_id: "sha256:aaa",
    tags: ["xorcise/mis-idor:1"],
    slug: "idor",
    size_bytes: 700_000_000,
    reason: "superseded",
  },
  {
    image_id: "sha256:bbb",
    tags: ["xorcise/mis-sqli:3"],
    slug: "sqli",
    size_bytes: 500_000_000,
    reason: "not installed",
  },
];

const report = (over: Partial<PruneReport> = {}): PruneReport => ({
  applied: false,
  candidates: CANDIDATES,
  removed: [],
  failed: [],
  reclaimable_bytes: 1_200_000_000,
  ...over,
});

/** Answer POST /missions/prune by its `apply` flag and record every body sent. */
function servePrune(dry: PruneReport, applied: PruneReport) {
  const bodies: unknown[] = [];
  server.use(
    http.post("*/api/missions/prune", async ({ request }) => {
      const body = (await request.json()) as { apply: boolean };
      bodies.push(body);
      return HttpResponse.json(body.apply ? applied : dry);
    }),
  );
  return bodies;
}

describe("PruneImagesPanel", () => {
  it("shows the dry run's candidates, reasons and reclaimable size without removing anything", async () => {
    const bodies = servePrune(report(), report({ applied: true }));
    renderWithProviders(<PruneImagesPanel />);

    fireEvent.click(screen.getByRole("button", { name: "Check for unused images" }));
    const table = await screen.findByRole("table");
    expect(bodies).toEqual([{ apply: false }]);
    expect(screen.getByText(/2 unused images · up to/)).toHaveTextContent("up to 1.2 GB reclaimable");

    const rows = within(table).getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(rows).toEqual([
      "idorxorcise/mis-idor:1Superseded by a newer version700.0 MB",
      "sqlixorcise/mis-sqli:3Mission not installed500.0 MB",
    ]);
  });

  it("removes only after an explicit confirmation, then lists each image it could not remove", async () => {
    const bodies = servePrune(
      report(),
      report({
        applied: true,
        removed: ["sha256:aaa"],
        failed: [
          { image_id: "sha256:bbb", tags: ["xorcise/mis-sqli:3"], error: "image is in use by a container" },
        ],
      }),
    );
    renderWithProviders(<PruneImagesPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Check for unused images" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove 2 images…" }));

    const dialog = screen.getByRole("dialog", { name: "Remove 2 unused images?" });
    expect(dialog).toHaveTextContent("Frees up to 1.2 GB.");
    expect(bodies).toEqual([{ apply: false }]);
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove images" }));

    const result = await screen.findByTestId("prune-result");
    expect(bodies).toEqual([{ apply: false }, { apply: true }]);
    expect(result).toHaveTextContent("Removed 1 of 2 images.");
    expect(result).toHaveTextContent("1 image was not removed:");
    expect(result).toHaveTextContent("xorcise/mis-sqli:3 — image is in use by a container");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("sends nothing when the confirmation is cancelled", async () => {
    const bodies = servePrune(report(), report({ applied: true }));
    renderWithProviders(<PruneImagesPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Check for unused images" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove 2 images…" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(bodies).toEqual([{ apply: false }]);
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("says plainly when there is nothing to reclaim", async () => {
    servePrune(report({ candidates: [], reclaimable_bytes: 0 }), report({ applied: true }));
    renderWithProviders(<PruneImagesPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Check for unused images" }));
    expect(await screen.findByText("No unused mission images. Nothing to reclaim.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^remove/i })).not.toBeInTheDocument();
  });

  it("shows the server's 409 refusal as it is, not an empty report", async () => {
    const detail =
      "this server does not manage Docker images (stub adapters); run prune on the runner host";
    server.use(
      http.post("*/api/missions/prune", () => HttpResponse.json({ detail }, { status: 409 })),
    );
    renderWithProviders(<PruneImagesPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Check for unused images" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(detail);
    expect(screen.queryByText(/Nothing to reclaim/)).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("keeps the dialog open until removal ends, then announces the outcome and returns focus", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.post("*/api/missions/prune", async ({ request }) => {
        const { apply } = (await request.json()) as { apply: boolean };
        if (!apply) return HttpResponse.json(report());
        await gate;
        return HttpResponse.json(report({ applied: true, removed: ["sha256:aaa", "sha256:bbb"] }));
      }),
    );
    renderWithProviders(<PruneImagesPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Check for unused images" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove 2 images…" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove images" }));

    // While removing, nothing can close the dialog as if the removal were called back.
    await within(dialog).findByRole("button", { name: "Removing…" });
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent("Removed 2 of 2 images.");
    expect(screen.getByRole("button", { name: "Check again" })).toHaveFocus();
  });

  it("says so when the list changed between the check and the removal", async () => {
    servePrune(
      report(),
      report({ applied: true, candidates: [CANDIDATES[0]], removed: ["sha256:aaa"] }),
    );
    renderWithProviders(<PruneImagesPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Check for unused images" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove 2 images…" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Remove images" }));

    const result = await screen.findByTestId("prune-result");
    expect(result).toHaveTextContent("Removed 1 of 1 image.");
    expect(result).toHaveTextContent("The list changed after the check (2 images then).");
  });

  it("shows a refusal of the removal itself in the server's words and claims nothing removed", async () => {
    const detail = "an install record could not be read: missions/idor/install.json";
    server.use(
      http.post("*/api/missions/prune", async ({ request }) => {
        const { apply } = (await request.json()) as { apply: boolean };
        return apply
          ? HttpResponse.json({ detail }, { status: 409 })
          : HttpResponse.json(report());
      }),
    );
    renderWithProviders(<PruneImagesPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Check for unused images" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove 2 images…" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Remove images" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(detail);
    expect(screen.queryByTestId("prune-result")).not.toBeInTheDocument();
    expect(screen.queryByText(/^Removed /)).not.toBeInTheDocument();
  });
});
