import { describe, it, expect, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { MissionCard } from "./mission-card";
import type { CatalogEntry, PullJobView } from "@/lib/api/types";

const LIBRARY: CatalogEntry = {
  source: "library",
  mission_id: "sqli-login",
  name: "SQLi Login",
  summary: "A login bypass",
  installed: false,
  skills: [],
  technologies: [],
  platforms: [],
};
const INSTALLED: CatalogEntry = { ...LIBRARY, mission_id: "idor", name: "IDOR", installed: true };

const job = (over: Partial<PullJobView> = {}): PullJobView => ({
  job_id: "j1",
  mission_id: "sqli-login",
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

// Every mounted card resolves the active pull job for its mission (reload-resume), so a
// null-job default handler must exist before component-specific overrides.
beforeEach(() => {
  server.use(
    http.get("*/api/missions/pull-jobs", () => HttpResponse.json(null)),
  );
});

describe("MissionCard pull (job-based)", () => {
  it("starts a pull job and shows live byte progress until installed", async () => {
    let pulled = "";
    let finish = false; // flipped by the test once the progress UI is asserted
    server.use(
      http.post("*/api/missions/sqli-login/pull-jobs", () => {
        pulled = "sqli-login";
        return HttpResponse.json({ job_id: "j1" }, { status: 202 });
      }),
      http.get("*/api/missions/pull-jobs/j1", () =>
        HttpResponse.json(
          finish
            ? job({ status: "installed", phase: "done", percent: 100, eta_seconds: 0 })
            : job({
                bytes_current: 434_000_000,
                bytes_total: 1_200_000_000,
                percent: 36.2,
                eta_seconds: 42,
              }),
        ),
      ),
    );
    renderWithProviders(<MissionCard mission={LIBRARY} />);
    fireEvent.click(await screen.findByRole("button", { name: /pull/i }));
    await waitFor(() => expect(pulled).toBe("sqli-login"));

    // While pulling: a DETERMINATE bar with bytes + ETA caption; the button is disabled.
    await waitFor(() => {
      expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "36");
    });
    expect(screen.getByText(/434\.0 MB \/ 1\.2 GB/)).toBeInTheDocument();
    expect(screen.getByText(/~0:42 left/)).toBeInTheDocument();
    expect(screen.getByText(/Downloading image/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /pulling/i })).toBeDisabled();

    // The next poll tick lands the terminal state.
    finish = true;
    await waitFor(
      () => expect(screen.getByText("Installed — ready to run.")).toBeInTheDocument(),
      { timeout: 4000 },
    );
  }, 10_000);

  it("resumes an in-flight pull on mount with an indeterminate bar while total unknown", async () => {
    // Reload-resume: the active-job lookup finds a live job this component never started.
    server.use(
      http.get("*/api/missions/pull-jobs", () => HttpResponse.json(job())),
      http.get("*/api/missions/pull-jobs/j1", () => HttpResponse.json(job())),
    );
    renderWithProviders(<MissionCard mission={LIBRARY} />);
    const bar = await screen.findByRole("progressbar");
    expect(bar).not.toHaveAttribute("aria-valuenow"); // total unknown → indeterminate
    expect(screen.getByRole("button", { name: /pulling/i })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /^pull$/i })).toBeNull();
  });

  it("shares one live job across two views of the same mission", async () => {
    server.use(
      http.post("*/api/missions/sqli-login/pull-jobs", () =>
        HttpResponse.json({ job_id: "j1" }, { status: 202 }),
      ),
      http.get("*/api/missions/pull-jobs/j1", () => HttpResponse.json(job())),
    );
    renderWithProviders(
      <>
        <MissionCard mission={LIBRARY} />
        <MissionCard mission={LIBRARY} />
      </>,
    );
    // Start the pull from the FIRST card only.
    fireEvent.click((await screen.findAllByRole("button", { name: /^pull$/i }))[0]);
    // Both views observe the shared job: two disabled Pulling buttons, no Pull left.
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: /pulling/i })).toHaveLength(2),
    );
    expect(screen.queryByRole("button", { name: /^pull$/i })).toBeNull();
  });

  it("surfaces the job's error detail and re-enables Pull", async () => {
    server.use(
      http.post("*/api/missions/sqli-login/pull-jobs", () =>
        HttpResponse.json({ job_id: "j1" }, { status: 202 }),
      ),
      http.get("*/api/missions/pull-jobs/j1", () =>
        HttpResponse.json(job({ status: "error", detail: "could not pull img: no registry" })),
      ),
    );
    renderWithProviders(<MissionCard mission={LIBRARY} />);
    fireEvent.click(await screen.findByRole("button", { name: /pull/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/Pull failed — could not pull img: no registry/),
      ).toBeInTheDocument(),
    );
    // The job is terminal, so the card offers Pull again.
    expect(screen.getByRole("button", { name: /^pull$/i })).toBeEnabled();
  });

  it("cancels an in-flight pull: Cancelling… while unwinding, then re-enables Pull", async () => {
    let cancelPosted = false;
    let unwound = false; // flipped by the test once the intermediate state is asserted
    server.use(
      // Mount already pulling (reload-resume) so the Cancel button has a live job_id.
      http.get("*/api/missions/pull-jobs", () => HttpResponse.json(job())),
      http.post("*/api/missions/pull-jobs/j1/cancel", () => {
        cancelPosted = true;
        // Server acks the cancel; status stays 'pulling' until the worker unwinds.
        return HttpResponse.json(job({ cancel_requested: true }));
      }),
      http.get("*/api/missions/pull-jobs/j1", () =>
        HttpResponse.json(
          unwound
            ? job({ status: "cancelled", cancel_requested: true })
            : job({ cancel_requested: cancelPosted }),
        ),
      ),
    );
    renderWithProviders(<MissionCard mission={LIBRARY} />);

    // While pulling the card offers Cancel alongside the disabled Pulling button.
    fireEvent.click(await screen.findByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(cancelPosted).toBe(true)); // the cancel hit the server

    // Intermediate state: cancel_requested && still pulling → a DISABLED "Cancelling…" button
    // (guards the `cancelRequested && status === "pulling"` branch of isCancelling).
    const cancelling = await screen.findByRole("button", { name: /cancelling/i });
    expect(cancelling).toBeDisabled();

    // The worker finishes unwinding → terminal 'cancelled': a note + Pull offered again.
    unwound = true;
    await waitFor(
      () => expect(screen.getByText("Pull cancelled.")).toBeInTheDocument(),
      { timeout: 4000 },
    );
    expect(screen.getByRole("button", { name: /^pull$/i })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /cancel/i })).toBeNull();
  });

  it("shows no Pull button for an installed mission", async () => {
    renderWithProviders(<MissionCard mission={INSTALLED} />);
    expect(screen.queryByRole("button", { name: /pull/i })).toBeNull();
  });
});

describe("MissionCard base-generation compatibility", () => {
  it("shows an 'update required' badge for an incompatible artifact, with the hint in its tooltip", async () => {
    renderWithProviders(
      <MissionCard
        mission={{
          ...LIBRARY,
          compatible: false,
          compat_hint: "Reinstall this mission to get the current base.",
        }}
      />,
    );
    const badge = await screen.findByText(/update required/i);
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveAttribute(
      "title",
      "Reinstall this mission to get the current base.",
    );
  });

  it("shows no compatibility badge for a runnable mission", async () => {
    renderWithProviders(<MissionCard mission={{ ...LIBRARY, compatible: true }} />);
    await waitFor(() =>
      expect(screen.getByText("SQLi Login")).toBeInTheDocument(),
    );
    expect(screen.queryByText(/update required/i)).toBeNull();
  });
});
