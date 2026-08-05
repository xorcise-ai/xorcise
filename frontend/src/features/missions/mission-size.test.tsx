import { describe, it, expect, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { MissionCard, MissionRow } from "./mission-card";
import type { CatalogEntry } from "@/lib/api/types";

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

  it("frames the size as an upper bound, since shared layers transfer less", async () => {
    renderWithProviders(<MissionCard mission={AVAILABLE} />);

    const size = await screen.findByTestId("mission-download-size");
    expect(size.textContent).toMatch(/up to/i);
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
