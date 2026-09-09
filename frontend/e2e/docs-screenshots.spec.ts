import { test, expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

/**
 * The documentation's screenshots, generated from the real console.
 *
 * Every image on docs.xorcise.ai used to be shot by hand. By September they were five weeks
 * stale and showed a console that no longer existed — the sidebar still said "Challenges"
 * (renamed to "Missions" in July), the buttons still used the pre-design-system uppercase
 * labels, and the Agents page was full of test debris like `test-cli-renamed`. Nobody had
 * done anything wrong; hand-shot images simply have no way to notice the product moved.
 *
 * So they are generated instead. This spec drives the real UI against a seeded throwaway
 * instance and writes PNGs the docs repository consumes directly.
 *
 * PREREQUISITES — this is not part of `npm run test:e2e`, because it needs seeded state and
 * writes files. Run it deliberately:
 *
 *   export XORCISE_HOME=/tmp/xorcise-docs XORCISE_REST_PORT=3051 XORCISE_OTLP_PORT=4351
 *   xorcise up --stub
 *   python scripts/seed_docs_state.py --rest http://127.0.0.1:3051 --otlp http://127.0.0.1:4351
 *   cd frontend && DOCS_RUN_ID=<the RUN_ID it printed> \
 *     PLAYWRIGHT_BASE_URL=http://127.0.0.1:3051 \
 *     npx playwright test e2e/docs-screenshots.spec.ts
 *
 * Output lands in `frontend/docs-screenshots/`. Copy into the docs repository's `images/`.
 *
 * WHY ELEMENT-SCOPED CROPS, NOT PIXEL BOXES: a step image wants the relevant control, not the
 * whole 1440px page. Cropping by locator means a moved button still produces a correct crop,
 * where a hardcoded bounding box silently photographs the wrong thing. It is also why there
 * are no baked-in numbered markers: an annotated PNG cannot be regenerated without a human
 * re-annotating it, which is the failure mode this whole spec exists to remove.
 */

const OUT = join(process.cwd(), "docs-screenshots");
/**
 * Two runs, because no single run renders both things the docs need. A run still in `created`
 * shows the connect prompt and the launch-mode toggle but draws "No terrain yet" — the map
 * appears once the run has left that state. A sealed run draws the full terrain and has a
 * result, but has no prompt left to hand anyone. So the seed makes both.
 */
const RUN_ID = process.env.DOCS_RUN_ID ?? "";              // sealed: terrain + result
const PENDING_RUN_ID = process.env.DOCS_PENDING_RUN_ID ?? ""; // created: prompt + launch mode

/** Retina. The docs render these at up to ~1400 CSS px, so 1x looks soft on any modern display. */
const VIEWPORT = { width: 1440, height: 900 } as const;

test.use({ viewport: VIEWPORT, deviceScaleFactor: 2, colorScheme: "dark" });

test.beforeAll(() => {
  mkdirSync(OUT, { recursive: true });
});

/**
 * Wait for the page to stop moving. `networkidle` alone is not enough: the terrain map and the
 * timeline animate in after their data arrives, and a screenshot taken mid-transition shows
 * half-drawn edges. The extra settle is empirical, not superstition.
 */
async function settle(page: Page, ms = 1200): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(ms);
}

async function shot(page: Page, name: string): Promise<void> {
  await page.screenshot({ path: join(OUT, `${name}.png`) });
}

test.describe("full-page console screenshots", () => {
  test("dashboard", async ({ page }) => {
    await page.goto("/ui/");
    await settle(page);
    await shot(page, "dashboard");
  });

  test("agents", async ({ page }) => {
    await page.goto("/ui/agents/");
    await expect(page.getByRole("heading", { name: "Agents", exact: true })).toBeVisible();
    await settle(page);
    await shot(page, "agents");
  });

  test("mission catalog", async ({ page }) => {
    await page.goto("/ui/missions/");
    await expect(page.getByRole("heading", { name: "Mission Catalog" })).toBeVisible();
    await settle(page);
    await shot(page, "missions");
  });

  test("run history", async ({ page }) => {
    await page.goto("/ui/runs/");
    await expect(page.getByRole("heading", { name: "Run history" })).toBeVisible();
    await settle(page);
    await shot(page, "runs");
  });

  test("performance", async ({ page }) => {
    await page.goto("/ui/results/");
    await expect(page.getByRole("heading", { name: "Performance" })).toBeVisible();
    await settle(page);
    await shot(page, "performance");
  });

  test("settings", async ({ page }) => {
    await page.goto("/ui/settings/");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await settle(page);
    await shot(page, "settings");
  });

  test("setup", async ({ page }) => {
    await page.goto("/ui/setup/");
    await settle(page);
    await shot(page, "setup");
  });
});

test.describe("the live run page", () => {
  test.skip(!RUN_ID, "DOCS_RUN_ID not set — seed an instance first (see the header comment)");

  test("live run with terrain and trace", async ({ page }) => {
    await page.goto(`/ui/runs/live/?id=${RUN_ID}`);
    // The terrain map is the slowest thing on the page and the point of the screenshot.
    // Role-scoped: the string "Terrain" also appears in the empty state, and a bare
    // getByText matches both.
    await expect(page.getByRole("heading", { name: "Terrain", exact: true })).toBeVisible();
    await expect(page.getByText("No terrain yet.")).toHaveCount(0);
    await settle(page, 2500);
    await shot(page, "traces");
  });

  test("run result", async ({ page }) => {
    await page.goto(`/ui/runs/result/?id=${RUN_ID}`);
    await settle(page);
    await shot(page, "results");
  });
});

/**
 * Step crops for guides/connect-your-agent, whose Web UI tabs were a sentence each with no
 * image at all. Each crop is the control the step names — the crop IS the annotation.
 */
test.describe("step crops for connect-your-agent", () => {
  test("register an agent", async ({ page }) => {
    await page.goto("/ui/agents/new/");
    await settle(page);
    // The form card, not the whole page: the step is "fill this in", not "look at the console".
    const form = page.locator("form").first();
    await expect(form).toBeVisible();
    await form.screenshot({ path: join(OUT, "step-register-agent.png") });
  });

  test("create a run", async ({ page }) => {
    await page.goto("/ui/runs/new/");
    await expect(page.getByRole("heading", { name: "New run" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Select agent" })).toBeVisible();
    await settle(page);
    await shot(page, "step-new-run");
  });

  /**
   * NOT capturable under `--stub`, and the reason is worth recording rather than rediscovering.
   *
   * The prompt card renders only while the run is `awaitingAgent`, which needs the run's
   * environment to reach `ready`. A stub environment never leaves `starting` — nothing is
   * actually deployed — so the card never mounts. Sending telemetry does not help either: that
   * marks the agent as connected, which ends the awaiting state from the other side.
   *
   * So this shot needs a REAL instance (Docker, a pulled mission, a deployed environment),
   * photographed in the window after the environment is ready and before the agent connects.
   * It skips rather than fails, so a stub-mode run of this spec is still green — but it does
   * NOT silently write nothing, because the skip reason is the record.
   */
  test("the connect prompt and launch-mode toggle", async ({ page }) => {
    test.skip(!PENDING_RUN_ID, "DOCS_PENDING_RUN_ID not set");
    await page.goto(`/ui/runs/live/?id=${PENDING_RUN_ID}`);
    await settle(page, 2000);
    const toggle = page.getByText(/this host/i).first();
    test.skip(
      (await toggle.count()) === 0,
      "no launch-mode toggle on this run — the prompt card needs a READY environment, which " +
        "stub mode never reaches. Re-run against a real instance to capture this crop.",
    );
    await shot(page, "step-launch-mode");
  });
});
