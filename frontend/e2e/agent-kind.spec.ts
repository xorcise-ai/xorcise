import { test, expect, type Page } from "@playwright/test";

/**
 * The Register agent page's harness picker in a real browser (#141).
 *
 * jsdom has no layout, so two things only a browser can show are pinned here: the first click
 * on a harness card selects it even though it blurs the autofocused Name field, and a custom
 * Harness ID no registered harness answers to is disclosed before submit yet still registers.
 *
 * Runs against a fresh install. Each test registers at most one uniquely named agent and
 * deletes it afterwards, so it leaves the server as it found it.
 */

const created: string[] = [];

test.afterEach(async ({ request }) => {
  for (const name of created.splice(0)) {
    await request.delete(`/api/agents/${encodeURIComponent(name)}`);
  }
});

// The app shell has its own role="status" (the toast region), so match on the warning's text.
const kindWarning = (page: Page) =>
  page.getByRole("status").filter({ hasText: "isn’t a registered harness" });

async function openRegisterPage(page: Page) {
  await page.goto("/ui/agents/new/");
  // Hydrated: React has applied autoFocus, which is what makes the next click a blur.
  await expect(page.getByPlaceholder("scout")).toBeFocused();
}

test("the first click on a harness card selects it", async ({ page }) => {
  await openRegisterPage(page);
  const custom = page.getByRole("radio", { name: /^Custom/ });
  const before = await custom.boundingBox();
  await custom.click();
  // The click is also the Name field's blur, so its message appears: the cards must not move.
  await expect(page.getByText("Name is required.")).toBeVisible();
  expect(await custom.boundingBox()).toEqual(before);
  await expect(custom).toHaveAttribute("aria-checked", "true");
  await expect(page.getByLabel("Harness ID")).toBeEditable();
});

test("a near-miss Harness ID is flagged and one click fixes it", async ({ page }) => {
  await openRegisterPage(page);
  await page.getByRole("radio", { name: /^Custom/ }).click();
  await page.getByLabel("Harness ID").fill("openhand");

  const warning = kindWarning(page);
  await expect(warning).toContainText("“openhand” isn’t a registered harness. Did you mean OpenHands?");

  await warning.getByRole("button", { name: "Use OpenHands" }).click();
  await expect(page.getByRole("radio", { name: /^OpenHands/ })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByLabel("Harness ID")).toHaveValue("openhands");
  await expect(warning).toHaveCount(0);
});

test("an unrecognised custom kind still registers, and its edit page says so", async ({ page }) => {
  const name = `e2e-custom-${Date.now()}`;
  await openRegisterPage(page);
  await page.getByPlaceholder("scout").fill(name);
  await page.getByRole("radio", { name: /^Custom/ }).click();
  await page.getByLabel("Harness ID").fill("my-agent-cli");
  await expect(kindWarning(page)).toContainText("“my-agent-cli” isn’t a registered harness.");

  const saved = page.waitForResponse(
    (r) => r.url().endsWith("/api/agents") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Register agent" }).click();
  const response = await saved;
  created.push(name);
  expect(response.status()).toBe(201);
  const body = await response.json();
  expect(body.kind).toBe("my-agent-cli");
  expect(body.warnings).toHaveLength(1);
  expect(body.warnings[0]).toContain("'my-agent-cli' is not a registered harness");
  await expect(page).toHaveURL(/\/ui\/agents\/$/);

  await page.goto(`/ui/agents/new/?agent=${encodeURIComponent(name)}`);
  await expect(page.getByRole("heading", { name: `Edit “${name}”` })).toBeVisible();
  await expect(kindWarning(page)).toContainText("“my-agent-cli” isn’t a registered harness.");
});
