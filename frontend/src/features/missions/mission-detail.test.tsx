import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { ToastHost } from "@/components/ui/toast";
import { useToastStore } from "@/stores/toasts";
import { MissionDetail } from "./mission-detail";
import type { CatalogEntry } from "@/lib/api/types";

// The detail page routes back to the catalog after a delete; record where it goes.
const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

// The detail page resolves the active pull job for its mission (reload-resume);
// default to "no active job".
beforeEach(() => {
  push.mockReset();
  useToastStore.setState({ toasts: [] });
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
  platforms: [],
};

const LAB: CatalogEntry = {
  ...INSTALLED,
  type: "lab",
  image: "xorcise/mis-idor:1",
  image_size_bytes: 722_600_000,
};

/** Serve `entry` as the only mission, record the DELETE's query string, and answer it as the
 *  server does: a bare 204 without purge, the purge body with it. */
function serveDelete(entry: CatalogEntry, purgeBody?: object) {
  const sent: { query: string | null } = { query: null };
  server.use(
    http.get("*/api/missions", () => HttpResponse.json([entry])),
    http.delete("*/api/missions/idor", ({ request }) => {
      sent.query = new URL(request.url).search;
      return purgeBody && sent.query === "?purge=true"
        ? HttpResponse.json(purgeBody)
        : new HttpResponse(null, { status: 204 });
    }),
  );
  return sent;
}

/** Render the page with a toast host, wait for it, and press the header's Delete. */
async function openDialog() {
  renderWithProviders(
    <>
      <MissionDetail id="idor" />
      <ToastHost />
    </>,
  );
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "IDOR" })).toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
  return screen.getByRole("dialog", { name: "Delete IDOR?" });
}

describe("MissionDetail delete", () => {
  it("confirms in a dialog that keeps the image by default, then uninstalls", async () => {
    const sent = serveDelete(LAB);
    const dialog = await openDialog();
    expect(sent.query).toBeNull();
    const box = within(dialog).getByRole("checkbox", {
      name: "Also remove its image (722.6 MB)",
    });
    expect(box).not.toBeChecked();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete mission" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/missions"));
    expect(sent.query).toBe("");
    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it("starts unchecked again each time the dialog opens", async () => {
    serveDelete(LAB);
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /also remove its image/i }));
    expect(within(dialog).getByRole("checkbox", { name: /also remove its image/i })).toBeChecked();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(
      within(screen.getByRole("dialog", { name: "Delete IDOR?" })).getByRole("checkbox", {
        name: /also remove its image/i,
      }),
    ).not.toBeChecked();
  });

  it.each([
    ["removed", "removed xorcise/mis-idor:1", "Removed its image"],
    ["kept", "kept xorcise/mis-idor:1: used by an active run", "Kept its image"],
    ["none", "xorcise/mis-idor:1 is not in the local image store", "No image removed"],
    ["failed", "conflict: image is in use", "Uninstalled, but could not remove its image"],
  ])("ticked, purges and reports a %s image", async (outcome, detail, title) => {
    const sent = serveDelete(LAB, {
      mission_id: "idor",
      image: "xorcise/mis-idor:1",
      image_outcome: outcome,
      detail,
    });
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /also remove its image/i }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete mission" }));
    await waitFor(() => expect(screen.getByText(title)).toBeInTheDocument());
    expect(sent.query).toBe("?purge=true");
    expect(screen.getByText(detail)).toBeInTheDocument();
    expect(push).toHaveBeenCalledWith("/missions");
  });

  it("omits the size when the entry does not carry one", async () => {
    serveDelete({ ...LAB, image_size_bytes: null });
    const dialog = await openDialog();
    expect(
      within(dialog).getByRole("checkbox", { name: "Also remove its image" }),
    ).not.toBeChecked();
  });

  it("offers no image removal for a static mission, which has no image", async () => {
    const sent = serveDelete({ ...INSTALLED, type: "static", image: "" });
    const dialog = await openDialog();
    expect(within(dialog).getByText(/recorded runs and results are kept/i)).toBeInTheDocument();
    expect(within(dialog).queryByRole("checkbox")).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete mission" }));
    await waitFor(() => expect(sent.query).toBe(""));
  });

  it("shows a refusal inline in the server's words and stays open", async () => {
    server.use(
      http.get("*/api/missions", () => HttpResponse.json([LAB])),
      http.delete("*/api/missions/idor", () =>
        HttpResponse.json(
          { detail: "mission 'idor' has an active run; terminate it first" },
          { status: 409 },
        ),
      ),
    );
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete mission" }));
    expect(
      await within(dialog).findByText("mission 'idor' has an active run; terminate it first"),
    ).toBeInTheDocument();
    // Past the dialog's 150ms exit, so a dialog that closed would be gone by now.
    await act(() => new Promise((r) => setTimeout(r, 200)));
    expect(screen.getByRole("dialog", { name: "Delete IDOR?" })).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
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
