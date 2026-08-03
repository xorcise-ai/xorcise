import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture, agentFixture, missionFixture } from "@/test/fixtures";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
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

import { RunList } from "./run-list";
import { NewRunForm } from "./new-run-form";

describe("RunList", () => {
  it("renders runs with their state badge", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "r1", mission: "sqli-login", state: "active" }),
          runFixture({
            run_id: "r2",
            mission: "idor",
            state: "terminal",
            terminal_trigger: "done",
          }),
        ]),
      ),
    );
    renderWithProviders(<RunList />);
    // Scope card assertions to the run list — the filter facets also render the mission,
    // agent, and status names as <option>s.
    const list = await screen.findByRole("list");
    const cards = within(list);
    expect(
      cards.getByRole("link", { name: /sqli-login/i }),
    ).toBeInTheDocument();
    // §10 status vocabulary: capitalised labels, state-appropriate actions.
    expect(cards.getByText("Running")).toBeInTheDocument();
    expect(cards.getByText("Completed")).toBeInTheDocument();
    expect(cards.getByText("Open Live Run")).toBeInTheDocument();
    expect(cards.getByText("View Result")).toBeInTheDocument();
  });

  it("filters runs by search text and shows a Clear filters affordance", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "r1", mission: "sqli-login", state: "active" }),
          runFixture({
            run_id: "r2",
            mission: "idor",
            state: "terminal",
            terminal_trigger: "done",
          }),
        ]),
      ),
    );
    renderWithProviders(<RunList />);
    const list = await screen.findByRole("list");
    expect(
      within(list).getByRole("link", { name: /sqli-login/i }),
    ).toBeInTheDocument();
    expect(
      within(list).getByRole("link", { name: /idor/i }),
    ).toBeInTheDocument();

    // No filter active → no Clear filters affordance (report §17 tertiary action).
    expect(screen.queryByText("Clear filters")).not.toBeInTheDocument();

    // Frontend search narrows to the matching mission only.
    fireEvent.change(screen.getByLabelText("Search runs"), {
      target: { value: "sqli" },
    });
    await waitFor(() =>
      expect(
        within(list).queryByRole("link", { name: /idor/i }),
      ).not.toBeInTheDocument(),
    );
    expect(
      within(list).getByRole("link", { name: /sqli-login/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Clear filters")).toBeInTheDocument();
  });

  // Core principle: the page shell must not scroll. The results grid is the one
  // unbounded region, so the filter bar it belongs to can never scroll away with
  // the cards it controls. jsdom has no layout, so this asserts the STRUCTURE
  // that produces the behaviour: the scroller is an ancestor of the grid and not
  // of the filter chrome.
  it("scrolls the results grid internally, not the page (filters stay put)", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "r1", mission: "sqli-login", state: "active" }),
        ]),
      ),
    );
    const { container } = renderWithProviders(<RunList />);

    const list = await screen.findByRole("list");
    const scroller = list.closest(".overflow-y-auto");
    expect(scroller).not.toBeNull();

    // The search input is chrome: outside the scrolling region.
    const search = screen.getByLabelText("Search runs");
    expect(scroller!.contains(search)).toBe(false);

    // The page root itself owns no scrollbar — it is a bounded flex column.
    const root = container.firstElementChild!;
    expect(root.className).toContain("h-full");
    expect(root.className).not.toContain("overflow-y-auto");
  });

  it("shows a filtered empty state when nothing matches", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "r1", mission: "sqli-login", state: "active" }),
        ]),
      ),
    );
    renderWithProviders(<RunList />);
    const list = await screen.findByRole("list");
    expect(
      within(list).getByRole("link", { name: /sqli-login/i }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search runs"), {
      target: { value: "zzz-no-match" },
    });
    await waitFor(() =>
      expect(
        screen.getByText("No runs match these filters."),
      ).toBeInTheDocument(),
    );
  });
});

describe("NewRunForm", () => {
  it("creates a 1:1:1 run and routes to its live page", async () => {
    push.mockClear();
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({
            mission_id: "sqli-login",
            name: "SQLi Login Bypass",
            installed: true,
          }),
          missionFixture({
            mission_id: "not-installed",
            name: "Library Mission",
            installed: false,
          }),
        ]),
      ),
      http.post("*/api/runs", () =>
        HttpResponse.json(
          { ...runFixture({ run_id: "run-new" }), run_control_key: "k" },
          { status: 201 },
        ),
      ),
    );
    renderWithProviders(<NewRunForm />);

    // Agent is picked from its roster of cards (no dropdown).
    fireEvent.click(await screen.findByRole("button", { name: "scout" }));

    // Mission is chosen from the picker (clickable rows); both installed and
    // library missions appear (the server auto-pulls a library one on start).
    fireEvent.click(
      await screen.findByRole("button", { name: /SQLi Login Bypass/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Start Run/i }));

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/runs/live?id=run-new"),
    );
  });

  it("discloses only the chosen intel via intel_policy on create", async () => {
    push.mockClear();
    let posted: { intel_policy?: string } | null = null;
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({
            mission_id: "sqli-login",
            name: "SQLi Login Bypass",
            installed: true,
          }),
        ]),
      ),
      // This mission has two authored intel, so the disclosure control is live.
      http.get("*/api/missions/sqli-login/manifest", () =>
        HttpResponse.json({
          schema_version: "2.0",
          metadata: {
            mission_id: "sqli-login",
            name: "SQLi Login Bypass",
            summary: "",
            objective: "",
          },
          environment: { compose_file: "docker-compose.yml", entry_networks: [] },
          artifacts: [],
          rubric: [],
          checks: [],
          intel: [
            { id: "i1", text: "Try a tautology in the username." },
            { id: "i2", text: "Comment out the password check." },
          ],
          attachments: [],
          terrain: null,
        }),
      ),
      http.post("*/api/runs", async ({ request }) => {
        posted = (await request.json()) as { intel_policy?: string };
        return HttpResponse.json(
          { ...runFixture({ run_id: "run-new" }), run_control_key: "k" },
          { status: 201 },
        );
      }),
    );
    renderWithProviders(<NewRunForm />);

    fireEvent.click(await screen.findByRole("button", { name: "scout" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /SQLi Login Bypass/i }),
    );

    // The checklist appears once the mission's intel load (defaulting to all disclosed). Clear to
    // none, then disclose only the first intel (checkbox rows are labelled by intel id).
    fireEvent.click(await screen.findByRole("button", { name: "None" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /^i1\b/ }));
    fireEvent.click(screen.getByRole("button", { name: /Start Run/i }));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted!.intel_policy).toBe("i1");
    expect(push).toHaveBeenCalledWith("/runs/live?id=run-new");
  });

  it("defaults to disclosing all intel; None then All round-trips to all", async () => {
    push.mockClear();
    let posted: { intel_policy?: string } | null = null;
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({
            mission_id: "sqli-login",
            name: "SQLi Login Bypass",
            installed: true,
          }),
        ]),
      ),
      http.get("*/api/missions/sqli-login/manifest", () =>
        HttpResponse.json({
          schema_version: "2.0",
          metadata: {
            mission_id: "sqli-login",
            name: "SQLi Login Bypass",
            summary: "",
            objective: "",
          },
          environment: { compose_file: "docker-compose.yml", entry_networks: [] },
          artifacts: [],
          rubric: [],
          checks: [],
          intel: [
            { id: "i1", text: "First." },
            { id: "i2", text: "Second." },
          ],
          attachments: [],
          terrain: null,
        }),
      ),
      http.post("*/api/runs", async ({ request }) => {
        posted = (await request.json()) as { intel_policy?: string };
        return HttpResponse.json(
          { ...runFixture({ run_id: "run-new" }), run_control_key: "k" },
          { status: 201 },
        );
      }),
    );
    renderWithProviders(<NewRunForm />);

    fireEvent.click(await screen.findByRole("button", { name: "scout" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /SQLi Login Bypass/i }),
    );
    // Default is all disclosed; clearing to None then All returns to disclosing everything, which
    // is sent as the stable "all" policy rather than an exhaustive id list.
    fireEvent.click(await screen.findByRole("button", { name: "None" }));
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    fireEvent.click(screen.getByRole("button", { name: /Start Run/i }));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted!.intel_policy).toBe("all");
  });

  it("puts intel disclosure in its own step under the brief, not the launch step", async () => {
    // Intel are mission-scoped content (graduated spoilers about THIS mission), so their
    // step (Select intel) sits in the mission column, under the brief — which also keeps the
    // launch column from overflowing when a intel-heavy mission loads.
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({
            mission_id: "sqli-login",
            name: "SQLi Login Bypass",
            installed: true,
          }),
        ]),
      ),
      http.get("*/api/missions/sqli-login/manifest", () =>
        HttpResponse.json({
          schema_version: "2.0",
          metadata: {
            mission_id: "sqli-login",
            name: "SQLi Login Bypass",
            summary: "",
            objective: "",
          },
          environment: { compose_file: "docker-compose.yml", entry_networks: [] },
          artifacts: [],
          rubric: [],
          checks: [],
          intel: [
            { id: "i1", text: "Try a tautology in the username." },
            { id: "i2", text: "Comment out the password check." },
          ],
          attachments: [],
          terrain: null,
        }),
      ),
    );
    renderWithProviders(<NewRunForm />);

    await screen.findByRole("button", { name: "scout" });
    fireEvent.click(
      await screen.findByRole("button", { name: /SQLi Login Bypass/i }),
    );

    // The checklist renders under the "Select intel" step heading...
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: /^i1\b/ }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("checkbox", { name: /^i2\b/ })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Select intel" }),
    ).toBeInTheDocument();

    // ...not inside the brief panel (Mission info is pure reading matter)...
    const brief = await screen.findByTestId("mission-brief");
    expect(within(brief).queryByRole("checkbox", { name: /^i1\b/ })).toBeNull();

    // ...and the launch step no longer owns the intel either.
    const launch = screen.getByText("Review & launch").closest("div")!
      .parentElement as HTMLElement;
    expect(within(launch).queryByRole("checkbox", { name: /^i1\b/ })).toBeNull();
    expect(screen.queryByText("Configure run")).toBeNull();
  });

  it("numbers the steps in linear reading order across the columns", async () => {
    // The redesign's whole point: 1→2 (selections, column 1), 3→4 (mission info + intel,
    // column 2), 5→6 (configure, then review & launch, column 3) — the numbering never
    // zig-zags back across the screen, and each step is one atomic card.
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({
            mission_id: "sqli-login",
            name: "SQLi Login Bypass",
            installed: true,
          }),
        ]),
      ),
    );
    renderWithProviders(<NewRunForm />);
    await screen.findByRole("button", { name: "scout" });

    const steps = screen
      .getAllByRole("heading", { level: 2 })
      .map((h) => h.textContent);
    expect(steps).toEqual([
      "Select agent",
      "Select mission",
      "Mission info",
      "Select intel",
      "Configure",
      "Review & launch",
    ]);
  });

  it("shows a live disclosed-count in the Select intel step header", async () => {
    // A count in the step's header ("N of M") makes the disclosure choice legible at a
    // glance — and updates as the operator toggles.
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({
            mission_id: "sqli-login",
            name: "SQLi Login Bypass",
            installed: true,
          }),
        ]),
      ),
      http.get("*/api/missions/sqli-login/manifest", () =>
        HttpResponse.json({
          schema_version: "2.0",
          metadata: {
            mission_id: "sqli-login",
            name: "SQLi Login Bypass",
            summary: "",
            objective: "",
          },
          environment: { compose_file: "docker-compose.yml", entry_networks: [] },
          artifacts: [],
          rubric: [],
          checks: [],
          intel: [
            { id: "i1", text: "First." },
            { id: "i2", text: "Second." },
          ],
          attachments: [],
          terrain: null,
        }),
      ),
    );
    renderWithProviders(<NewRunForm />);

    await screen.findByRole("button", { name: "scout" });
    fireEvent.click(
      await screen.findByRole("button", { name: /SQLi Login Bypass/i }),
    );

    // The intel live under their own numbered step heading, a peer of the other steps.
    const intelHeading = await screen.findByRole("heading", {
      name: "Select intel",
    });
    // Scope to the step card: the Review card mirrors the same "N of M" in its Intel row.
    const intelStep = intelHeading.closest("div")!.parentElement as HTMLElement;
    // Defaults to all disclosed → "2 of 2" in the step header.
    await waitFor(() =>
      expect(within(intelStep).getByText("2 of 2")).toBeInTheDocument(),
    );
    // Clearing to none updates the header live.
    fireEvent.click(within(intelStep).getByRole("button", { name: "None" }));
    expect(within(intelStep).getByText("0 of 2")).toBeInTheDocument();
  });

  it("clamps a long mission description behind a Show more toggle", async () => {
    const longSummary =
      "A cheerful new social network built for birds is live on a single web host, and its " +
      "operators suspect it hides a flaw. On the surface the server looks buttoned-up: version " +
      "banners are suppressed and directory scanners come back almost entirely empty, so the " +
      "finding is deliberately un-telegraphed and success hinges on reading HTTP responses closely.";
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({
            mission_id: "aviary",
            name: "Aviary Access",
            installed: true,
            summary: longSummary,
          }),
        ]),
      ),
      http.get("*/api/missions/aviary/manifest", () =>
        HttpResponse.json({
          schema_version: "2.0",
          metadata: {
            mission_id: "aviary",
            name: "Aviary Access",
            summary: "",
            objective: "",
          },
          environment: { compose_file: "docker-compose.yml", entry_networks: [] },
          artifacts: [],
          rubric: [],
          checks: [],
          intel: [],
          attachments: [],
          terrain: null,
        }),
      ),
    );
    renderWithProviders(<NewRunForm />);

    await screen.findByRole("button", { name: "scout" });
    fireEvent.click(await screen.findByRole("button", { name: /Aviary Access/i }));

    // Collapsed by default: the paragraph is line-clamped and a Show more control is offered.
    const summary = await screen.findByText(/A cheerful new social network/);
    expect(summary.className).toContain("line-clamp-7");
    const more = screen.getByRole("button", { name: /Show more/i });
    fireEvent.click(more);

    // Expanded: the clamp is lifted and the control flips to Show less.
    expect(summary.className).not.toContain("line-clamp-7");
    expect(screen.getByRole("button", { name: /Show less/i })).toBeInTheDocument();
  });
});
