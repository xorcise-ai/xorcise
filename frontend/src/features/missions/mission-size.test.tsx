import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { MissionCard, MissionRow } from "./mission-card";
import { MissionDetail } from "./mission-detail";
import type { CatalogEntry } from "@/lib/api/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

/**
 * The download size is a decision input BEFORE a pull, so it has to reach the card
 * while the mission is still "available". It is deliberately absent once installed —
 * the bytes are already on disk — and absent from a catalog that predates the field,
 * which must degrade to no badge rather than a confident "0 B".
 */

const AVAILABLE: CatalogEntry = {
  source: "library",
  mission_id: "chrono-canary",
  name: "Chrono Canary",
  summary: "Own the time service",
  installed: false,
  skills: [],
  technologies: [],
  image_size_bytes: 260306509,
  attachments_size_bytes: 384284,
  download_size_bytes: 260690793,
};

beforeEach(() => {
  server.use(http.get("*/api/missions/pull-jobs", () => HttpResponse.json(null)));
});

describe("MissionCard download size", () => {
  it("quotes the download size while the mission is still available", async () => {
    renderWithProviders(<MissionCard mission={AVAILABLE} />);

    expect(await screen.findByText(/260\.7 MB/)).toBeInTheDocument();
  });

  it("shows the bare figure on the card, with no hedging language", async () => {
    renderWithProviders(<MissionCard mission={AVAILABLE} />);

    const size = await screen.findByTestId("mission-download-size");
    expect(size.textContent?.trim()).toBe("260.7 MB");
  });

  it("keeps the shared-layer caveat in the tooltip, where the bare figure would mislead", async () => {
    /* The figure is a ceiling, not a prediction: 17 of 19 lab missions share one base
       image, so a second pull transfers roughly half. The card stays clean and the
       caveat lives on hover rather than in the label. */
    renderWithProviders(<MissionCard mission={AVAILABLE} />);

    const size = await screen.findByTestId("mission-download-size");
    expect(size.getAttribute("title")).toMatch(/not re-downloaded/i);
    expect(size.getAttribute("title")).toMatch(/transfers less/i);
  });

  it("renders no size for an installed mission — the download already happened", () => {
    renderWithProviders(
      <MissionCard mission={{ ...AVAILABLE, installed: true, download_size_bytes: null }} />,
    );

    expect(screen.queryByTestId("mission-download-size")).not.toBeInTheDocument();
  });

  it("renders no size when the catalog serves none, rather than 0 B", () => {
    renderWithProviders(
      <MissionCard mission={{ ...AVAILABLE, download_size_bytes: null }} />,
    );

    expect(screen.queryByTestId("mission-download-size")).not.toBeInTheDocument();
    expect(screen.queryByText(/0 B/)).not.toBeInTheDocument();
  });

  it("breaks the total down into image and attachments for the curious", async () => {
    renderWithProviders(<MissionCard mission={AVAILABLE} />);

    const size = await screen.findByTestId("mission-download-size");
    expect(size.getAttribute("title")).toMatch(/260\.3 MB/); // image
    expect(size.getAttribute("title")).toMatch(/384\.3 KB/); // attachments
  });

  it("gives a static mission no tooltip at all — nothing left to qualify", async () => {
    renderWithProviders(
      <MissionCard
        mission={{
          ...AVAILABLE,
          image_size_bytes: null,
          attachments_size_bytes: 590400000,
          download_size_bytes: 590400000,
        }}
      />,
    );

    const size = await screen.findByTestId("mission-download-size");
    expect(size.getAttribute("title")).toBeNull();
  });

  it("omits the breakdown when the total is a single component", async () => {
    /* A mission with no attachments would otherwise read "280.5 MB — image 280.5 MB",
       stating the same number twice. A breakdown only earns its place when the figure
       actually decomposes. */
    renderWithProviders(
      <MissionCard
        mission={{
          ...AVAILABLE,
          attachments_size_bytes: null,
          image_size_bytes: 280500000,
          download_size_bytes: 280500000,
        }}
      />,
    );

    const size = await screen.findByTestId("mission-download-size");
    expect(size.getAttribute("title")).not.toMatch(/image/i);
    expect(size.getAttribute("title")).toMatch(/not re-downloaded/i);
  });
});

describe("MissionRow download size", () => {
  it("quotes the size in the compact row too", async () => {
    renderWithProviders(
      <ul>
        <MissionRow mission={AVAILABLE} />
      </ul>,
    );

    expect(await screen.findByText(/260\.7 MB/)).toBeInTheDocument();
  });
});

describe("MissionDetail download size", () => {
  /* The detail page is the other place a pull gets decided, so it must quote the same
     cost. It has more room than a card, so it shows the image/attachment split outright
     instead of hiding it in a tooltip. */
  // Only the catalog row is stubbed. The manifest endpoint is deliberately left unhandled
  // so `manifest.data` stays undefined and the page takes its `m?.` guarded path — a
  // PARTIAL manifest fixture crashes the render outright (the rich sections index into
  // fields a stub omits), which reads as "the size never appeared". Same choice the other
  // mission-detail tests make. The size comes from the catalog row anyway, not the manifest.
  const serve = (entry: CatalogEntry) =>
    server.use(http.get("*/api/missions", () => HttpResponse.json([entry])));

  it("quotes the download size for a mission that is not yet installed", async () => {
    serve(AVAILABLE);

    renderWithProviders(<MissionDetail id="chrono-canary" />);

    const size = await screen.findByTestId("mission-detail-download-size");
    expect(size.textContent).toMatch(/260\.7 MB/);
  });

  it("breaks the figure into image and attachments, since there is room here", async () => {
    serve(AVAILABLE);

    renderWithProviders(<MissionDetail id="chrono-canary" />);

    const size = await screen.findByTestId("mission-detail-download-size");
    expect(size.textContent).toMatch(/260\.3 MB/); // image
    expect(size.textContent).toMatch(/384\.3 KB/); // attachments
  });

  it("omits the breakdown when the total is a single component", async () => {
    serve({
      ...AVAILABLE,
      attachments_size_bytes: null,
      image_size_bytes: 280500000,
      download_size_bytes: 280500000,
    });

    renderWithProviders(<MissionDetail id="chrono-canary" />);

    const size = await screen.findByTestId("mission-detail-download-size");
    expect(size.textContent).toMatch(/Download 280\.5 MB/);
    expect(size.textContent).not.toMatch(/image/i);
  });

  it("drops the shared-layer caveat for a static mission, which has no layers", async () => {
    /* A STATIC mission is an attachment bundle, not an image — nothing is layered and
       nothing is ever reused from disk, so its figure is exact. Repeating the layer
       caveat there states something that cannot happen. */
    serve({
      ...AVAILABLE,
      image_size_bytes: null,
      attachments_size_bytes: 590400000,
      download_size_bytes: 590400000,
    });

    renderWithProviders(<MissionDetail id="chrono-canary" />);

    const size = await screen.findByTestId("mission-detail-download-size");
    expect(size.textContent).toMatch(/Download 590\.4 MB/);
    expect(size.textContent).not.toMatch(/shared layers/i);
  });

  it("shows no size once the mission is installed", async () => {
    serve({ ...AVAILABLE, installed: true, source: "your_own", download_size_bytes: null });

    renderWithProviders(<MissionDetail id="chrono-canary" />);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Chrono Canary" })).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("mission-detail-download-size")).not.toBeInTheDocument();
  });
});
