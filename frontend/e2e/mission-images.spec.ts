import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Re-pull and prune in a real browser: the console's half of `mission pull --refresh` and
 * `mission prune`.
 *
 * `page.route` answers the mission and prune endpoints, so the spec runs against any served UI
 * (CI's fresh server has no installed mission and, with Docker, nothing to prune). Everything
 * else still reaches the server.
 */

const MISSION = {
  source: "library",
  mission_id: "e2e-lab",
  name: "E2E Lab",
  summary: "",
  installed: true,
  type: "lab",
  image: "xorcise/mis-e2e-lab:1",
  skills: [],
  technologies: [],
  platforms: [],
};

const PULLING = {
  job_id: "j-running",
  mission_id: "e2e-lab",
  status: "pulling",
  phase: "pulling_image",
  bytes_current: 200_000_000,
  bytes_total: 800_000_000,
  percent: 25,
  eta_seconds: null,
  detail: null,
  cancel_requested: false,
  entry: null,
};

const CANDIDATE = {
  image_id: "sha256:old",
  tags: ["xorcise/mis-e2e-lab:0"],
  slug: "e2e-lab",
  size_bytes: 650_000_000,
  reason: "superseded",
};

/** Route the mission endpoints this spec needs, recording each POST body by path. */
async function routeMissions(page: Page, prune: (apply: boolean) => [number, object]) {
  const posts: Array<{ path: string; body: unknown }> = [];
  await page.route(/\/api\/missions(\/|\?|$)/, async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^.*\/api/, "");
    if (req.method() === "POST") {
      const body = req.postDataJSON() ?? null;
      posts.push({ path, body });
      if (path === "/missions/prune") {
        const [status, json] = prune((body as { apply: boolean }).apply);
        return route.fulfill({ status, json });
      }
      if (path === "/missions/e2e-lab/pull-jobs") {
        return route.fulfill({ status: 202, json: { job_id: "j-running", joined: true } });
      }
    }
    if (path === "/missions") return route.fulfill({ json: [MISSION] });
    if (path === "/missions/pull-jobs") return route.fulfill({ json: null });
    if (path === "/missions/pull-jobs/j-running") return route.fulfill({ json: PULLING });
    return route.continue();
  });
  return posts;
}

test("Re-pull sends a refresh and says when it only joined a pull already running", async ({ page }) => {
  const posts = await routeMissions(page, () => [500, {}]);
  await page.goto("/ui/missions/detail/?id=e2e-lab");

  await page.getByRole("button", { name: "Re-pull" }).click();
  await expect(page.getByText(/already running, so this shows its progress instead of starting a refresh/)).toBeVisible();
  await expect(page.getByRole("progressbar").first()).toHaveAttribute("aria-valuenow", "25");
  await expect(page.getByRole("button", { name: "Cancel" })).toBeVisible();
  expect(posts).toEqual([{ path: "/missions/e2e-lab/pull-jobs", body: { refresh: true } }]);
});

test("prune shows the dry run, removes only after a confirmation, and lists what it could not remove", async ({ page }) => {
  const posts = await routeMissions(page, (apply) =>
    apply
      ? [200, {
          applied: true,
          candidates: [CANDIDATE],
          removed: [],
          failed: [{ ...CANDIDATE, error: "image is being used by a running container" }],
          reclaimable_bytes: 650_000_000,
        }]
      : [200, { applied: false, candidates: [CANDIDATE], removed: [], failed: [], reclaimable_bytes: 650_000_000 }],
  );
  await page.goto("/ui/settings/");

  await page.getByRole("button", { name: "Check for unused images" }).click();
  const row = page.getByRole("row", { name: /e2e-lab/ });
  await expect(row).toContainText("Superseded by a newer version");
  await expect(row).toContainText("650.0 MB");
  await expect(page.getByText("1 unused image · up to 650.0 MB reclaimable")).toBeVisible();

  await page.getByRole("button", { name: "Remove 1 image…" }).click();
  const dialog = page.getByRole("dialog", { name: "Remove 1 unused image?" });
  await expect(dialog).toContainText("Frees up to 650.0 MB.");
  await dialog.getByRole("button", { name: "Remove images" }).click();

  const result = page.getByTestId("prune-result");
  await expect(result).toContainText("Removed 0 of 1 image.");
  await expect(result).toContainText("xorcise/mis-e2e-lab:0 — image is being used by a running container");
  expect(posts.map((p) => p.body)).toEqual([{ apply: false }, { apply: true }]);
});

test("prune shows the server's refusal as it is", async ({ page }) => {
  const detail = "this server does not manage Docker images (role 'all', stubs forced)";
  await routeMissions(page, () => [409, { detail }]);
  await page.goto("/ui/settings/");

  await page.getByRole("button", { name: "Check for unused images" }).click();
  // Scoped to the card: Settings has its own table (Modules) that appears once its probe lands.
  const card = page.locator("[data-card]").filter({ hasText: "Mission images" });
  await expect(card.getByRole("alert")).toHaveText(detail);
  await expect(card.getByRole("table")).toHaveCount(0);
});
