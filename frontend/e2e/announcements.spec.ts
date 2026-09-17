import { test, expect } from "@playwright/test";

/**
 * The announcement banners in a real browser.
 *
 * The vitest suite pins behaviour; this pins the two things jsdom cannot see — that the shell
 * banner sits in normal flow under the header rather than overlaying the page, and that a long
 * unbroken token wraps instead of giving the document a horizontal scrollbar.
 *
 * `page.route` injects one announcement per placement, so the spec needs a served UI but not a
 * remote announcement service.
 */

const ANNOUNCEMENTS = {
  announcements: [
    {
      id: "e2e-app",
      revision: 1,
      placement: "application",
      tone: "maintenance",
      body_md: "Scheduled maintenance on Friday. See [the runbook](https://docs.xorcise.ai/x).",
      dismissible: true,
    },
    {
      id: "e2e-cat",
      revision: 1,
      placement: "catalog",
      tone: "information",
      body_md: "Pulls are slower than usual.",
      dismissible: true,
    },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/announcements", (route) =>
    route.fulfill({ json: ANNOUNCEMENTS }),
  );
});

test("the application banner sits under the header, in flow, and dismisses", async ({
  page,
}) => {
  // `/ui/`, not `/` — baseURL is the origin, and the app is mounted at /ui.
  await page.goto("/ui/");

  const banner = page.getByTestId("announcement-banner-application");
  await expect(banner).toContainText("Scheduled maintenance on Friday.");
  await expect(banner).toContainText("Maintenance");

  // In normal flow: the main region starts below the banner's bottom edge. An overlay would
  // have them intersecting.
  const box = (await banner.boundingBox())!;
  const mainBox = (await page.locator("main").boundingBox())!;
  expect(mainBox.y).toBeGreaterThanOrEqual(box.y + box.height - 1);

  // The link opens offsite, with the rel that keeps window.opener out of the destination.
  const link = banner.getByRole("link", { name: "the runbook" });
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");

  await banner.getByRole("button", { name: "Dismiss announcement" }).click();
  await expect(banner).toHaveCount(0);
});

test("a maintenance banner does not claim the assertive alert role", async ({ page }) => {
  await page.goto("/ui/");
  await expect(page.getByTestId("announcement-banner-application")).toBeVisible();

  // Exclude Next's route announcer: it is an always-empty role="alert" div the App Router
  // injects into every page, so a bare [role="alert"] matches it and fails strict mode.
  await expect(page.locator('[role="alert"]:not(#__next-route-announcer__)')).toHaveCount(0);
  await expect(page.getByTestId("announcement-banner-application")).toHaveAttribute(
    "role",
    "status",
  );
});

test("a long unbroken token wraps instead of scrolling the page sideways", async ({
  page,
}) => {
  await page.route("**/api/announcements", (route) =>
    route.fulfill({
      json: {
        announcements: [
          {
            ...ANNOUNCEMENTS.announcements[0],
            body_md: "A".repeat(300),
            dismissible: false,
          },
        ],
      },
    }),
  );
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto("/ui/");

  await expect(page.getByTestId("announcement-banner-application")).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});

test("the catalog banner appears only on the XORCISE Remote tab", async ({ page }) => {
  await page.goto("/ui/missions/");

  await page.getByRole("tab", { name: /XORCISE Remote/i }).click();
  await expect(page.getByTestId("announcement-banner-catalog")).toContainText(
    "Pulls are slower than usual.",
  );

  await page.getByRole("tab", { name: /Your Own/i }).click();
  await expect(page.getByTestId("announcement-banner-catalog")).toHaveCount(0);
});
