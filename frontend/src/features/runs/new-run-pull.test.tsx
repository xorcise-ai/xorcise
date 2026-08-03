import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { agentFixture, missionFixture, runFixture } from "@/test/fixtures";
import type { PullJobView } from "@/lib/api/types";

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

import { NewRunForm } from "./new-run-form";

const job = (over: Partial<PullJobView> = {}): PullJobView => ({
  job_id: "j1",
  mission_id: "lib-1",
  status: "pulling",
  phase: "pulling_image",
  bytes_current: 0,
  bytes_total: 0,
  percent: null,
  eta_seconds: null,
  detail: null,
  cancel_requested: false,
  entry: null,
  ...over,
});

/**
 * Starting a run on a NOT-installed mission used to be an opaque wait: POST /runs pulls
 * the mission server-side inside the create request, so the operator saw a spinner for
 * however long a multi-GB image takes. The page now runs the catalog's job-based pull
 * first — same percent / bytes / phase / ETA readout the catalog shows — and creates the
 * run only once the job reports `installed`.
 */
describe("NewRunForm pull-before-start", () => {
  beforeEach(() => {
    push.mockClear();
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
      // Active-job lookup (reload-resume): nothing in flight by default.
      http.get("*/api/missions/pull-jobs", () => HttpResponse.json(null)),
    );
  });

  function library() {
    server.use(
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({
            mission_id: "lib-1",
            name: "Library Mission",
            installed: false,
          }),
        ]),
      ),
    );
  }

  it("shows download progress, then creates the run once the pull lands", async () => {
    let pulled = false;
    let finish = false;
    let runBody: Record<string, unknown> | null = null;
    library();
    server.use(
      http.post("*/api/missions/lib-1/pull-jobs", () => {
        pulled = true;
        return HttpResponse.json({ job_id: "j1" }, { status: 202 });
      }),
      http.get("*/api/missions/pull-jobs/j1", () =>
        HttpResponse.json(
          finish
            ? job({ status: "installed", phase: "done", percent: 100 })
            : job({
                bytes_current: 434_000_000,
                bytes_total: 1_200_000_000,
                percent: 36.2,
                eta_seconds: 42,
              }),
        ),
      ),
      http.post("*/api/runs", async ({ request }) => {
        runBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { ...runFixture({ run_id: "run-42" }), run_control_key: "k" },
          { status: 201 },
        );
      }),
    );

    renderWithProviders(
      <NewRunForm initialAgent="scout" initialMission="lib-1" />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "scout" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );

    // The action names the extra work up front.
    const start = await screen.findByRole("button", {
      name: /Download & Start Run/i,
    });
    fireEvent.click(start);

    // The pull job is kicked BEFORE the run is created.
    await waitFor(() => expect(pulled).toBe(true));
    expect(runBody).toBeNull();

    // Same live readout as the catalog: determinate bar + phase / bytes / ETA.
    await waitFor(() =>
      expect(screen.getByRole("progressbar")).toHaveAttribute(
        "aria-valuenow",
        "36",
      ),
    );
    expect(screen.getByText(/434\.0 MB \/ 1\.2 GB/)).toBeInTheDocument();
    expect(screen.getByText(/~0:42 left/)).toBeInTheDocument();
    expect(screen.getByText(/Downloading image/)).toBeInTheDocument();

    // Job completes → the run the operator already asked for is created and opened.
    finish = true;
    await waitFor(() => expect(runBody).not.toBeNull(), { timeout: 4000 });
    expect(runBody).toMatchObject({
      agent: "scout",
      mission: "lib-1",
      budget_seconds: 600,
    });
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/runs/live?id=run-42"),
    );
  }, 10_000);

  it("cancels the download and abandons the run it was the first half of", async () => {
    // A multi-GB image takes minutes; the form committed the operator to "Download & Start Run"
    // with no way out short of reloading. Cancelling must stop the pull AND clear the committed
    // state — otherwise a worker that finished installing before it saw the cancel would go on to
    // start the very run that was just called off.
    let cancelled = false;
    let runCreated = false;
    library();
    server.use(
      http.post("*/api/missions/lib-1/pull-jobs", () =>
        HttpResponse.json({ job_id: "j1" }, { status: 202 }),
      ),
      http.get("*/api/missions/pull-jobs/j1", () =>
        HttpResponse.json(
          cancelled
            ? job({ status: "cancelled", phase: "done", cancel_requested: true })
            : job({ percent: 20 }),
        ),
      ),
      http.post("*/api/missions/pull-jobs/j1/cancel", () => {
        cancelled = true;
        return HttpResponse.json(job({ status: "pulling", cancel_requested: true }));
      }),
      http.post("*/api/runs", () => {
        runCreated = true;
        return HttpResponse.json(runFixture({ run_id: "nope" }), { status: 201 });
      }),
    );

    renderWithProviders(
      <NewRunForm initialAgent="scout" initialMission="lib-1" />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Download & Start Run/i }),
    );

    const cancel = await screen.findByRole("button", { name: /Cancel download/i });
    fireEvent.click(cancel);

    await waitFor(() => expect(cancelled).toBe(true), { timeout: 4000 });
    // The outcome is stated rather than the panel silently disappearing. The terminal `cancelled`
    // status only arrives on the NEXT poll tick (the cancel response still reads `pulling` while
    // the worker unwinds), so this needs more than waitFor's 1s default.
    await waitFor(
      () => expect(screen.getByText(/Download cancelled/i)).toBeInTheDocument(),
      { timeout: 4000 },
    );
    // No run was started, and the form hands control back for another attempt.
    expect(runCreated).toBe(false);
    await waitFor(
      () =>
        expect(
          screen.getByRole("button", { name: /Download & Start Run/i }),
        ).toBeEnabled(),
      { timeout: 4000 },
    );
  }, 10_000);

  it("re-attaches to an in-flight pull on mount (status survives navigation / reload)", async () => {
    // A pull for this mission is already running server-side — started here then navigated away
    // from, or kicked from the catalog. The hook keys off the mission id, so a FRESHLY mounted
    // form resolves the active job and shows its progress with NO operator action: the status is
    // recovered, not lost.
    library();
    const inflight = job({
      job_id: "j9",
      bytes_current: 600_000_000,
      bytes_total: 1_200_000_000,
      percent: 50,
    });
    server.use(
      http.get("*/api/missions/pull-jobs", ({ request }) =>
        HttpResponse.json(
          new URL(request.url).searchParams.get("mission_id") === "lib-1"
            ? inflight
            : null,
        ),
      ),
      http.get("*/api/missions/pull-jobs/j9", () => HttpResponse.json(inflight)),
    );

    renderWithProviders(
      <NewRunForm initialAgent="scout" initialMission="lib-1" />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "scout" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );

    // No click — the in-flight pull's live progress appears on mount.
    await waitFor(() =>
      expect(screen.getByRole("progressbar")).toHaveAttribute(
        "aria-valuenow",
        "50",
      ),
    );
    expect(screen.getByText(/Downloading mission/i)).toBeInTheDocument();
  });

  it("surfaces a failed download and lets the operator try again", async () => {
    let attempts = 0;
    library();
    server.use(
      http.post("*/api/missions/lib-1/pull-jobs", () => {
        attempts += 1;
        return HttpResponse.json({ job_id: "j1" }, { status: 202 });
      }),
      http.get("*/api/missions/pull-jobs/j1", () =>
        HttpResponse.json(
          job({ status: "error", detail: "registry unreachable" }),
        ),
      ),
    );

    renderWithProviders(
      <NewRunForm initialAgent="scout" initialMission="lib-1" />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "scout" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Start Run/i }),
    );

    await waitFor(() =>
      expect(screen.getByText(/registry unreachable/)).toBeInTheDocument(),
    );
    // No run was created, and the action is live again for a retry.
    const start = screen.getByRole("button", { name: /Start Run/i });
    expect(start).toBeEnabled();
    fireEvent.click(start);
    await waitFor(() => expect(attempts).toBe(2));
  }, 10_000);

  it("stops at ONE create attempt when the run fails after a successful pull", async () => {
    let runAttempts = 0;
    library();
    server.use(
      http.post("*/api/missions/lib-1/pull-jobs", () =>
        HttpResponse.json({ job_id: "j1" }, { status: 202 }),
      ),
      http.get("*/api/missions/pull-jobs/j1", () =>
        HttpResponse.json(job({ status: "installed", phase: "done", percent: 100 })),
      ),
      http.post("*/api/runs", () => {
        runAttempts += 1;
        return HttpResponse.json({ detail: "docker unreachable" }, { status: 503 });
      }),
    );

    renderWithProviders(
      <NewRunForm initialAgent="scout" initialMission="lib-1" />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "scout" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    fireEvent.click(await screen.findByRole("button", { name: /Start Run/i }));

    // The server's own reason must reach the operator: a 503 from a missing dependency
    // cannot be fixed by retrying, so "please try again" is actively misleading.
    await waitFor(() =>
      expect(
        screen.getByText(/Couldn’t start the run — docker unreachable/),
      ).toBeInTheDocument(),
    );
    // A failed create must not re-fire the post-pull auto-start.
    await new Promise((r) => setTimeout(r, 400));
    expect(runAttempts).toBe(1);
    expect(screen.getByRole("button", { name: /Start Run/i })).toBeEnabled();
  }, 10_000);

  it("starts an installed mission immediately — no pull job", async () => {
    let runBody: Record<string, unknown> | null = null;
    server.use(
      http.get("*/api/missions", () =>
        HttpResponse.json([
          missionFixture({
            mission_id: "sqli-login",
            name: "SQLi Login Bypass",
            installed: true,
          }),
        ]),
      ),
      http.post("*/api/runs", async ({ request }) => {
        runBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { ...runFixture({ run_id: "run-7" }), run_control_key: "k" },
          { status: 201 },
        );
      }),
    );

    renderWithProviders(
      <NewRunForm initialAgent="scout" initialMission="sqli-login" />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "scout" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    // Nothing to download, so the action stays the plain one.
    expect(
      screen.queryByRole("button", { name: /Download & Start Run/i }),
    ).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Start Run/i }));
    await waitFor(() => expect(runBody).not.toBeNull());
    expect(screen.queryByRole("progressbar")).toBeNull();
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/runs/live?id=run-7"),
    );
  });
});
