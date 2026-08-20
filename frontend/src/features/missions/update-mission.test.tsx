import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { MissionCard, MissionRow } from "./mission-card";
import { MissionDetail } from "./mission-detail";
import type { CatalogEntry } from "@/lib/api/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// §34/§35 surfacing: every place that SAYS "update available" must be able to DO the update.

const BASE: CatalogEntry = {
  source: "library",
  mission_id: "sqli-login",
  name: "SQLi Login",
  summary: "A login bypass",
  installed: true,
  skills: [],
  technologies: [],
  platforms: ["linux/amd64", "linux/arm64"],
  mission_version: "1.0.0",
  mission_base_version: "2.0.0",
  current_mission_version: "1.1.0",
  update_available: true,
  platform: "linux/arm64",
  emulated: false,
};

beforeEach(() => {
  // every mounted card resolves its active pull job (reload-resume)
  server.use(http.get("*/api/missions/pull-jobs", () => HttpResponse.json(null)));
});

describe("update action", () => {
  it("card: Update button posts to /missions/{id}/update and reports the result", async () => {
    let posted = "";
    server.use(
      http.post("*/api/missions/sqli-login/update", () => {
        posted = "sqli-login";
        return HttpResponse.json({ updated: true, entry: { ...BASE, mission_version: "1.1.0" } });
      }),
    );
    renderWithProviders(<MissionCard mission={BASE} />);
    fireEvent.click(screen.getByRole("button", { name: /update/i }));
    await waitFor(() => expect(posted).toBe("sqli-login"));
    expect(await screen.findByText(/updated to the current release/i)).toBeInTheDocument();
  });

  it("card: an already-current install reads as such, not as a change", async () => {
    server.use(
      http.post("*/api/missions/sqli-login/update", () =>
        HttpResponse.json({ updated: false, entry: BASE }),
      ),
    );
    renderWithProviders(<MissionCard mission={BASE} />);
    fireEvent.click(screen.getByRole("button", { name: /update/i }));
    expect(await screen.findByText(/already up to date/i)).toBeInTheDocument();
  });

  it("card: a failed update surfaces the server's reason", async () => {
    server.use(
      http.post("*/api/missions/sqli-login/update", () =>
        HttpResponse.json({ detail: "registry unreachable" }, { status: 502 }),
      ),
    );
    renderWithProviders(<MissionCard mission={BASE} />);
    fireEvent.click(screen.getByRole("button", { name: /update/i }));
    expect(await screen.findByText(/update failed — registry unreachable/i)).toBeInTheDocument();
  });

  it("card: no update state ⇒ no Update button", () => {
    renderWithProviders(
      <MissionCard mission={{ ...BASE, update_available: false, current_mission_version: null }} />,
    );
    expect(screen.queryByRole("button", { name: /update/i })).not.toBeInTheDocument();
  });

  it("row: shows the update badge and the same action", async () => {
    let posted = false;
    server.use(
      http.post("*/api/missions/sqli-login/update", () => {
        posted = true;
        return HttpResponse.json({ updated: true, entry: BASE });
      }),
    );
    renderWithProviders(
      <ul>
        <MissionRow mission={BASE} />
      </ul>,
    );
    expect(screen.getByText("update available")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /update/i }));
    await waitFor(() => expect(posted).toBe(true));
  });

  it("detail: renders the update banner with the version delta and the action", async () => {
    server.use(
      http.get("*/api/missions", () => HttpResponse.json([BASE])),
      http.get("*/api/missions/sqli-login/manifest", () => HttpResponse.json(null)),
      http.get("*/api/missions/sqli-login/terrain", () => HttpResponse.json(null)),
    );
    renderWithProviders(<MissionDetail id="sqli-login" />);
    const banner = await screen.findByTestId("mission-update-banner");
    expect(banner).toHaveTextContent("Update available");
    expect(banner).toHaveTextContent("Mission 1.0.0 → 1.1.0");
    expect(screen.getByRole("button", { name: /update/i })).toBeInTheDocument();
  });
});

describe("platform surfacing", () => {
  it("card: validated platforms render as tags", () => {
    renderWithProviders(<MissionCard mission={{ ...BASE, update_available: false }} />);
    expect(screen.getByText("amd64")).toBeInTheDocument();
    expect(screen.getByText("arm64")).toBeInTheDocument();
  });

  it("card + row: an emulated install is flagged", () => {
    renderWithProviders(<MissionCard mission={{ ...BASE, emulated: true }} />);
    expect(screen.getByText("emulated")).toBeInTheDocument();
  });

  it("card: a pre-contract row shows no platform tags and no flags", () => {
    renderWithProviders(
      <MissionCard
        mission={{
          ...BASE,
          platforms: [],
          platform: null,
          emulated: null,
          update_available: null,
          mission_version: null,
          current_mission_version: null,
        }}
      />,
    );
    expect(screen.queryByText("amd64")).not.toBeInTheDocument();
    expect(screen.queryByText("emulated")).not.toBeInTheDocument();
    expect(screen.queryByText("update available")).not.toBeInTheDocument();
  });
});
