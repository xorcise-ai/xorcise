import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { MissionDetail } from "./mission-detail";
import { MissionCard } from "./mission-card";
import type { CatalogEntry, PullJobView } from "@/lib/api/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const INSTALLED_LIBRARY: CatalogEntry = {
  source: "library",
  mission_id: "idor",
  name: "IDOR",
  summary: "",
  installed: true,
  type: "lab",
  image: "xorcise/mis-idor:1",
  skills: [],
  technologies: [],
  platforms: [],
};

const job = (over: Partial<PullJobView> = {}): PullJobView => ({
  job_id: "j1",
  mission_id: "idor",
  status: "pulling",
  phase: "pulling_image",
  bytes_current: 300_000_000,
  bytes_total: 600_000_000,
  percent: 50,
  eta_seconds: null,
  detail: null,
  cancel_requested: false,
  entry: null,
  ...over,
});

/** Serve the one mission, answer the pull-job POST with `started`, and record its body. */
function serve(entry: CatalogEntry, started: { job_id: string; joined: boolean }) {
  const sent: { posted: boolean; body: unknown } = { posted: false, body: undefined };
  let polled = job();
  server.use(
    http.get("*/api/missions", () => HttpResponse.json([entry])),
    http.get("*/api/missions/pull-jobs", () => HttpResponse.json(null)),
    http.post("*/api/missions/idor/pull-jobs", async ({ request }) => {
      sent.posted = true;
      sent.body = await request.json().catch(() => undefined);
      return HttpResponse.json(started, { status: 202 });
    }),
    http.get("*/api/missions/pull-jobs/j1", () => HttpResponse.json(polled)),
  );
  return {
    sent,
    finish: () => {
      polled = job({ status: "installed", phase: "done", percent: 100 });
    },
  };
}

async function renderDetail() {
  renderWithProviders(<MissionDetail id="idor" />);
  await screen.findByRole("heading", { name: "IDOR" });
}

beforeEach(() => {
  server.use(http.get("*/api/missions/pull-jobs", () => HttpResponse.json(null)));
});

describe("Re-pull on the mission detail page", () => {
  it("sends refresh: true and shows the pull's progress and Cancel until it finishes", async () => {
    const { sent, finish } = serve(INSTALLED_LIBRARY, { job_id: "j1", joined: false });
    await renderDetail();

    const button = screen.getByRole("button", { name: "Re-pull" });
    // A plain pull skips layers already on disk, so Re-pull says it removes the image first.
    expect(button).toHaveAttribute(
      "title",
      "Remove this mission's image and download it again, to repair a damaged copy",
    );
    fireEvent.click(button);
    await waitFor(() => expect(sent.body).toEqual({ refresh: true }));
    await waitFor(() =>
      expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50"),
    );
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /pulling/i })).toBeDisabled();

    finish();
    // The next poll tick (1 s) lands the terminal state; RTL's default wait is also 1 s.
    expect(
      await screen.findByText("Re-pulled. The image was downloaded again and the mission reinstalled.", {}, { timeout: 3000 }),
    ).toBeInTheDocument();
  });

  it("says when the request only joined a pull that was already running, while that pull runs", async () => {
    const { finish } = serve(INSTALLED_LIBRARY, { job_id: "j1", joined: true });
    await renderDetail();

    fireEvent.click(screen.getByRole("button", { name: "Re-pull" }));
    const notice =
      /already running, so this shows its progress instead of starting a refresh\. Re-pull again once it finishes/;
    expect(await screen.findByText(notice)).toBeInTheDocument();

    // When the joined pull ends, the notice goes and nothing claims a refresh happened.
    finish();
    await waitFor(() => expect(screen.queryByText(notice)).not.toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.queryByText("Re-pulled. The image was downloaded again and the mission reinstalled.")).not.toBeInTheDocument();
  });

  it("says a cancelled Re-pull may have left the mission without its image", async () => {
    const { sent } = serve(INSTALLED_LIBRARY, { job_id: "j1", joined: false });
    server.use(
      http.get("*/api/missions/pull-jobs/j1", () =>
        HttpResponse.json(job({ status: "cancelled", phase: "done" })),
      ),
    );
    await renderDetail();
    fireEvent.click(screen.getByRole("button", { name: "Re-pull" }));
    await waitFor(() => expect(sent.posted).toBe(true));
    expect(
      await screen.findByText(
        "Re-pull cancelled. If it had already removed the image, Re-pull again before starting a run.",
        {},
        { timeout: 3000 },
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Pull cancelled.")).not.toBeInTheDocument();
  });

  it("is not offered for a custom mission, which has nothing to pull from", async () => {
    serve({ ...INSTALLED_LIBRARY, source: "your_own" }, { job_id: "j1", joined: false });
    await renderDetail();
    expect(screen.getByRole("button", { name: /^delete$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Re-pull" })).not.toBeInTheDocument();
  });

  it("is not offered before the mission is installed, where Pull still sends no body", async () => {
    const { sent } = serve({ ...INSTALLED_LIBRARY, installed: false }, { job_id: "j1", joined: false });
    await renderDetail();
    expect(screen.queryByRole("button", { name: "Re-pull" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Pull" }));
    await waitFor(() => expect(sent.posted).toBe(true));
    expect(sent.body).toBeUndefined();
  });
});

describe("An installed mission's catalog card during a re-pull", () => {
  it("shows the running pull's progress and lets it be cancelled", async () => {
    server.use(
      http.get("*/api/missions/pull-jobs", () => HttpResponse.json(job())),
      http.get("*/api/missions/pull-jobs/j1", () => HttpResponse.json(job())),
    );
    renderWithProviders(<MissionCard mission={INSTALLED_LIBRARY} />);

    await waitFor(() =>
      expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50"),
    );
    expect(screen.getByText(/300\.0 MB \/ 600\.0 MB/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel pull" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^open$/i })).toBeInTheDocument();
  });
});
