import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { MissionDetail } from "./mission-detail";
import type { CatalogEntry } from "@/lib/api/types";

// The detail page routes back to the catalog after a delete — stub the app router.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// The detail page resolves the active pull job for its mission (reload-resume);
// default to "no active job".
beforeEach(() => {
  server.use(
    http.get("*/api/missions/pull-jobs", () => HttpResponse.json(null)),
  );
});

const INSTALLED: CatalogEntry = {
  source: "your_own",
  mission_id: "idor",
  name: "IDOR",
  summary: "",
  installed: true,
  skills: [],
  technologies: [],
};

describe("MissionDetail delete", () => {
  it("confirms, then DELETEs an installed mission", async () => {
    let deleted = "";
    server.use(
      http.get("*/api/missions", () => HttpResponse.json([INSTALLED])),
      http.delete("*/api/missions/idor", () => {
        deleted = "idor";
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<MissionDetail id="idor" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "IDOR" })).toBeInTheDocument(),
    );
    // First click reveals a confirm; only the confirm actually deletes.
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(deleted).toBe("");
    fireEvent.click(screen.getByRole("button", { name: /yes, delete/i }));
    await waitFor(() => expect(deleted).toBe("idor"));
  });

  it("shows no Delete button for a not-installed library mission", async () => {
    server.use(
      http.get("*/api/missions", () =>
        HttpResponse.json([{ ...INSTALLED, source: "library", installed: false }]),
      ),
    );
    renderWithProviders(<MissionDetail id="idor" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "IDOR" })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /^delete$/i })).toBeNull();
    expect(screen.getByRole("button", { name: /^pull$/i })).toBeInTheDocument();
  });
});

describe("MissionDetail skills before pull", () => {
  it("lists security skills from the manifest for a not-yet-pulled library mission", async () => {
    // The catalog LIST entry for a library mission carries no skills (c.skills is empty), but
    // the manifest does — the detail page must show them before a pull, not "No skills listed."
    server.use(
      http.get("*/api/missions", () =>
        HttpResponse.json([
          { ...INSTALLED, source: "library", installed: false, skills: [] },
        ]),
      ),
      http.get("*/api/missions/idor/manifest", () =>
        HttpResponse.json({
          schema_version: "2.0",
          metadata: {
            mission_id: "idor",
            name: "IDOR",
            objective: "o",
            type: "lab",
            skills: ["web-exploitation", "authorization"],
            technologies: [],
          },
          environment: {},
          rubric: [],
          checks: [],
          artifacts: [],
          attachments: [],
          intel: [],
        }),
      ),
    );
    renderWithProviders(<MissionDetail id="idor" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "IDOR" })).toBeInTheDocument(),
    );
    // Skills come from the manifest even though the list entry has none.
    await waitFor(() =>
      expect(screen.getByText(/exploitation/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText("No skills listed.")).toBeNull();
  });
});

describe("MissionDetail terrain", () => {
  it("renders the ACTUAL terrain map (SVG renderer) for a manifest with a terrain block", async () => {
    server.use(
      http.get("*/api/missions", () => HttpResponse.json([INSTALLED])),
      http.get("*/api/missions/idor/manifest", () =>
        HttpResponse.json({
          schema_version: "2.0",
          metadata: {
            mission_id: "idor",
            name: "IDOR",
            objective: "o",
            type: "lab",
            skills: [],
            technologies: [],
          },
          environment: {},
          rubric: [],
          checks: [],
          artifacts: [],
          attachments: [],
          intel: [],
          terrain: {
            summary: "Reach the vault.",
            groups: [{ id: "dmz", label: "DMZ" }],
            nodes: [
              { id: "web", parent: "dmz", label: "web" },
              { id: "vault", parent: "dmz", label: "vault", objective: true },
            ],
            edges: [],
          },
        }),
      ),
      // The detail page draws the server-side projection — the SAME graph a run starts from.
      http.get("*/api/missions/idor/terrain", () =>
        HttpResponse.json({
          run_id: "",
          mission_id: "idor",
          summary: "Reach the vault.",
          groups: [
            { id: "dmz", label: "DMZ", description: null, kind: "segment", order: 2, hidden: false, discovered: false },
          ],
          nodes: [
            { id: "web", label: "web", group: "dmz", type: "service", objective: false,
              description: null, discovery_condition: null, completion_condition: null, state: "defined" },
            { id: "vault", label: "vault", group: "dmz", type: "service", objective: true,
              description: null, discovery_condition: null, completion_condition: null, state: "defined" },
          ],
          edges: [],
          updates: [],
          attribution: null,
          objective_id: "vault",
        }),
      ),
    );

    const { container } = renderWithProviders(<MissionDetail id="idor" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "IDOR" })).toBeInTheDocument(),
    );
    // The real SVG renderer, not the old linear flow simplification.
    await waitFor(() =>
      expect(container.querySelector('[data-testid="terrain-svg"]')).toBeInTheDocument(),
    );
    expect(container.querySelectorAll("[data-node-id]").length).toBe(2);
    // The objective keeps its ⊗ grammar on the preview too.
    expect(container.querySelector('[data-node-objective="true"]')).toBeInTheDocument();
    // The authored summary rides in the map's header strip.
    expect(screen.getByText("Reach the vault.")).toBeInTheDocument();
  });
});

describe("MissionDetail deterministic check prerequisites", () => {
  it("shows which check must pass before a dependent check earns credit", async () => {
    server.use(
      http.get("*/api/missions", () => HttpResponse.json([INSTALLED])),
      http.get("*/api/missions/idor/manifest", () =>
        HttpResponse.json({
          schema_version: "2.0",
          metadata: {
            mission_id: "idor",
            name: "IDOR",
            objective: "o",
            type: "lab",
            skills: [],
            technologies: [],
          },
          environment: {},
          rubric: [],
          checks: [
            {
              id: "flag-correct",
              source: "artifacts",
              ref: "flag",
              op: "observed",
              args: {},
              weight: 0.75,
              requires: [],
            },
            {
              id: "efficient-solve",
              source: "otel-stats",
              ref: "turn-count",
              op: "lesser_than",
              args: { value: 25 },
              weight: 0.25,
              requires: ["flag-correct"],
            },
          ],
          artifacts: [],
          attachments: [],
          intel: [],
        }),
      ),
    );

    renderWithProviders(<MissionDetail id="idor" />);

    const efficiency = await screen.findByRole("article", {
      name: "Efficient Solve deterministic check",
    });
    expect(within(efficiency).getByText("Requires")).toBeInTheDocument();
    expect(within(efficiency).getByText("Flag Correct")).toBeInTheDocument();

    const flag = screen.getByRole("article", {
      name: "Flag Correct deterministic check",
    });
    expect(within(flag).queryByText("Requires")).toBeNull();
  });
});

describe("MissionDetail base-generation compatibility", () => {
  it("shows the incompatibility banner when the artifact's base is not runnable", async () => {
    server.use(
      http.get("*/api/missions", () =>
        HttpResponse.json([
          {
            ...INSTALLED,
            compatible: false,
            base_major: 1,
            compat_hint: "Reinstall this mission to get the current base.",
          },
        ]),
      ),
    );
    renderWithProviders(<MissionDetail id="idor" />);

    const banner = await screen.findByTestId("mission-incompatible-banner");
    expect(within(banner).getByText(/Not runnable on this XORCISE/i)).toBeInTheDocument();
    expect(within(banner).getByText(/Reinstall this mission/i)).toBeInTheDocument();
    expect(within(banner).getByText(/built for base 1/i)).toBeInTheDocument();
  });

  it("shows no banner for a compatible mission", async () => {
    server.use(
      http.get("*/api/missions", () =>
        HttpResponse.json([{ ...INSTALLED, compatible: true }]),
      ),
    );
    renderWithProviders(<MissionDetail id="idor" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "IDOR" })).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("mission-incompatible-banner")).toBeNull();
  });
});
