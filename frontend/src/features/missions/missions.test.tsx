import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { missionFixture } from "@/test/fixtures";

// Every mounted card/detail resolves the active pull job for its mission
// (reload-resume); default to "no active job" so tests only override what they use.
beforeEach(() => {
  server.use(
    http.get("*/api/missions/pull-jobs", () => HttpResponse.json(null)),
  );
});

// MissionDetail routes back to the catalog after a delete — stub the app router.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { MissionCatalog } from "./catalog";
import { MissionDetail } from "./mission-detail";

const catalog = [
  missionFixture({
    mission_id: "sqli-login",
    name: "SQLi Login",
    source: "your_own",
    specialty: "web",
  }),
  missionFixture({
    mission_id: "pwn-1",
    name: "Stack Smash",
    source: "library",
    installed: false,
    specialty: "pwn",
  }),
];

describe("MissionCatalog", () => {
  it("separates providers into tabs (Your Own active first, Remote on switch)", async () => {
    server.use(http.get("*/api/missions", () => HttpResponse.json(catalog)));
    renderWithProviders(<MissionCatalog />);

    // Provider tabs present.
    expect(
      await screen.findByRole("tab", { name: /Your Own/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /XORCISE Remote/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Other providers/i })).toBeInTheDocument();

    // Your Own is active by default → its mission shows; the library one doesn't.
    await waitFor(() =>
      expect(screen.getByText("SQLi Login")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Stack Smash")).not.toBeInTheDocument();

    // Switch to the Remote tab → the library mission appears.
    fireEvent.click(screen.getByRole("tab", { name: /XORCISE Remote/i }));
    await waitFor(() =>
      expect(screen.getByText("Stack Smash")).toBeInTheDocument(),
    );
  });

  it("filters by search within a provider tab", async () => {
    server.use(http.get("*/api/missions", () => HttpResponse.json(catalog)));
    renderWithProviders(<MissionCatalog />);

    await waitFor(() =>
      expect(screen.getByText("SQLi Login")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("Search missions"), {
      target: { value: "sqli" },
    });
    // The Your Own tab still matches; switching to Remote shows nothing matching.
    expect(screen.getByText("SQLi Login")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /XORCISE Remote/i }));
    expect(screen.queryByText("Stack Smash")).not.toBeInTheDocument();
  });

  it("narrows the grid by SEVERAL specialties at once, and clears back to all", async () => {
    const many = [
      missionFixture({ mission_id: "a", name: "Web One", specialty: "web" }),
      missionFixture({ mission_id: "b", name: "Pwn One", specialty: "pwn" }),
      missionFixture({
        mission_id: "c",
        name: "Forensics One",
        specialty: "forensics",
      }),
    ];
    server.use(http.get("*/api/missions", () => HttpResponse.json(many)));
    renderWithProviders(<MissionCatalog />);

    await waitFor(() => expect(screen.getByText("Web One")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Specialty/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Web" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Pwn" }));

    // Union of the two, not the last one clicked.
    expect(screen.getByText("Web One")).toBeInTheDocument();
    expect(screen.getByText("Pwn One")).toBeInTheDocument();
    expect(screen.queryByText("Forensics One")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Clear filters/i }));
    expect(screen.getByText("Forensics One")).toBeInTheDocument();
  });

  it("opens on the tab that holds the missions when Your Own is dwarfed", async () => {
    server.use(
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({ mission_id: "mine", name: "My Own One" }),
          missionFixture({
            mission_id: "r1",
            name: "Remote One",
            source: "library",
            installed: false,
          }),
          missionFixture({
            mission_id: "r2",
            name: "Remote Two",
            source: "library",
            installed: false,
          }),
        ]),
      ),
    );
    renderWithProviders(<MissionCatalog />);
    await waitFor(() => expect(screen.getByText("Remote One")).toBeInTheDocument());
    expect(screen.queryByText("My Own One")).not.toBeInTheDocument();
  });

  it("shows the environment on an AVAILABLE card, before it is pulled", async () => {
    server.use(
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({
            mission_id: "static-one",
            name: "Static One",
            source: "library",
            installed: false,
            type: "static",
          }),
          missionFixture({
            mission_id: "untyped-one",
            name: "Untyped One",
            source: "library",
            installed: false,
            type: null,
          }),
        ]),
      ),
    );
    renderWithProviders(<MissionCatalog />);

    await waitFor(() => expect(screen.getByText("Static One")).toBeInTheDocument());
    // Not installed …
    expect(screen.getAllByText("available")).toHaveLength(2);
    // … yet the declared environment badge is already legible. Scoped by its tooltip title so it
    // targets the card badge, not the library-stats panel (which now also lists "Static").
    expect(screen.getByTitle(/attachment-only/)).toBeInTheDocument();
    // A row that declares no type renders no badge rather than a guessed one.
    expect(screen.queryByText("Lab")).not.toBeInTheDocument();
  });
});

describe("MissionDetail", () => {
  it("shows a not-found state for a missing mission", async () => {
    server.use(http.get("*/api/missions", () => HttpResponse.json([])));
    renderWithProviders(<MissionDetail id="nope" />);
    await waitFor(() =>
      expect(screen.getByText("Mission not found")).toBeInTheDocument(),
    );
  });

  it("renders the rich manifest (objective + expected artifacts)", async () => {
    server.use(
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({ mission_id: "sqli-login", name: "SQLi Login" }),
        ]),
      ),
      http.get("*/api/missions/sqli-login/manifest", () =>
        HttpResponse.json({
          schema_version: "2.0",
          metadata: {
            mission_id: "sqli-login",
            name: "SQLi Login",
            summary: "Bypass the login.",
            objective: "Bypass the login form via SQL injection.",
          },
          environment: { compose_file: "docker-compose.yml", entry_networks: [] },
          artifacts: [{ name: "flag.txt", description: "the admin flag", required: true }],
          rubric: [],
          checks: [],
          intel: [],
          attachments: [],
          terrain: null,
        }),
      ),
    );
    renderWithProviders(<MissionDetail id="sqli-login" />);
    await waitFor(() => expect(screen.getByText("Objective")).toBeInTheDocument());
    expect(
      screen.getByText("Bypass the login form via SQL injection."),
    ).toBeInTheDocument();
    expect(screen.getByText("Expected Artifacts")).toBeInTheDocument();
    expect(screen.getByText("flag.txt")).toBeInTheDocument();
  });

  it("pulls a library mission and reflects installed state", async () => {
    let installed = false;
    server.use(
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({
            mission_id: "pwn-1",
            name: "Stack Smash",
            source: "library",
            installed,
          }),
        ]),
      ),
      // Job-based pull: POST starts a background job; the poll reports it installed.
      http.post("*/api/missions/pwn-1/pull-jobs", () =>
        HttpResponse.json({ job_id: "j1" }, { status: 202 }),
      ),
      http.get("*/api/missions/pull-jobs/j1", () => {
        installed = true;
        return HttpResponse.json({
          job_id: "j1",
          mission_id: "pwn-1",
          status: "installed",
          phase: "done",
          bytes_current: 1024,
          bytes_total: 1024,
          percent: 100,
          eta_seconds: 0,
          detail: null,
          entry: null,
        });
      }),
    );
    renderWithProviders(<MissionDetail id="pwn-1" />);
    const pull = await screen.findByRole("button", { name: "Pull" });
    fireEvent.click(pull);
    await waitFor(() =>
      expect(screen.getByText("installed")).toBeInTheDocument(),
    );
  });
});
