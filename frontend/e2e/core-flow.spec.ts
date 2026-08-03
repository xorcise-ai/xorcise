import { test, expect } from "@playwright/test";

/**
 * CLI/UI parity and navigation completeness.
 *
 * Every core action is reachable from BOTH the CLI and the UI because both are
 * thin clients of the same server API. This e2e walks the UI side of the
 * register -> select mission -> create run -> watch -> result path and asserts
 * each surface is reachable with no orphan pages.
 *
 * CLI <-> UI parity map (same server endpoints back both):
 *   xorcise agent register   <-> /agents  (Register agent page)   POST /api/agents
 *   xorcise mission list     <-> /missions (catalog)              GET  /api/missions
 *   xorcise run create       <-> /runs/new (New run form)         POST /api/runs
 *   xorcise run list         <-> /runs                            GET  /api/runs
 *   (watch)                  <-> /runs/live?id=...                GET  /api/runs/{id}/traces
 *   (result)                 <-> /runs/result?id=...              GET  /api/runs/{id}/result
 *
 * Requires a running server (see playwright.config.ts). Written to pass against a
 * FRESH install — no agents, no runs, nothing installed — because that is what CI
 * gets, and a test that needs seeded state is one that only ever runs locally.
 */

/**
 * A nav label and the heading of the page it opens. These DIFFER, and assuming they
 * match is what rotted the previous version: the sidebar says "Missions" and "Runs"
 * while the pages are headed "Mission Catalog" and "Run history" — drift from the
 * mission/intel vocabulary rename that went uncaught because nothing ran this suite.
 * Keeping the pairs explicit makes the next rename fail loudly, right here.
 */
const NAV: ReadonlyArray<{ label: string; heading: string }> = [
  { label: "Agents", heading: "Agents" },
  { label: "Missions", heading: "Mission Catalog" },
  { label: "Runs", heading: "Run history" },
  { label: "Results", heading: "Performance" },
];

test("operator-surface navigation reaches every section", async ({ page }) => {
  await page.goto("/ui/");

  // `/` is a first-run ROUTER, not a fixed page: Welcome until the operator has runs
  // AND setup is ready, the Dashboard after (home-router.tsx). Asserting either
  // heading would tie this test to seeded state, so the invariant asserted here is
  // the shell — every destination is reachable from it in both states.
  const nav = page.getByRole("navigation").first();
  await expect(nav).toBeVisible();

  for (const { label, heading } of NAV) {
    await nav.getByRole("link", { name: label, exact: true }).first().click();
    await expect(
      page.getByRole("heading", { name: heading, exact: true }),
      `nav "${label}" should open the page headed "${heading}"`,
    ).toBeVisible();
  }

  // the create-run entry point is reachable from Runs
  await nav.getByRole("link", { name: "Runs", exact: true }).first().click();
  await page.getByRole("link", { name: /New run/i }).first().click();
  await expect(page.getByRole("heading", { name: "New run" })).toBeVisible();

  // …and the form offers both halves of a run. These are step SECTIONS, not labelled
  // controls: the previous version asserted getByLabel("Agent") and
  // getByLabel("Mission"), neither of which exists — the form's only <label> is
  // "Run name (optional)".
  await expect(page.getByRole("heading", { name: "Select agent" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Select mission" })).toBeVisible();
});

test("a deep link to a missing run renders a clean not-found", async ({ page }) => {
  await page.goto("/ui/runs/live/?id=does-not-exist");
  await expect(page.getByText("Run not found")).toBeVisible();
});
