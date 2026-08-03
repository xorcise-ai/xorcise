import { test, expect } from "@playwright/test";

/**
 * When the server is unreachable the UI shows a clear state, not a crash.
 *
 * Run this against the static export served WITHOUT a reachable /api (e.g. a
 * plain static file server, or point PLAYWRIGHT_BASE_URL at a stopped server).
 * The health poll fails and the shell renders the server-unreachable banner.
 *
 * Requires a served UI with /api unreachable (see playwright.config.ts).
 *
 * Tagged @no-server so the CI job can select it: it needs the OPPOSITE precondition
 * to the rest of the suite, and running it against a live server would assert that a
 * healthy server looks unreachable.
 */
test("@no-server shows the server-unreachable banner when /api is down", async ({
  page,
}) => {
  // `/ui/`, not `/` — baseURL is the origin, and the app is mounted at /ui.
  await page.goto("/ui/");

  // Exclude Next's route announcer. It is an always-empty `role="alert"` div that the
  // App Router injects into every page, so a bare getByRole("alert") matches two
  // elements and fails Playwright's strict mode before it ever reads the text.
  const banner = page.locator('[role="alert"]:not(#__next-route-announcer__)');
  await expect(banner).toContainText(/Can't reach the XORCISE server/i, {
    timeout: 15_000,
  });
});
