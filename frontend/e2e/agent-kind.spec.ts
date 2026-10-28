import { test, expect, type Page } from "@playwright/test";

/**
 * The Register agent page's harness picker in a real browser (#141).
 *
 * jsdom has no layout or stylesheet, so what only a browser can show is pinned here: the first
 * click on a harness card selects it even though it blurs the autofocused Name field; the
 * read-only Harness ID draws no focus ring; edit mode leaves no empty line under the Name field;
 * and your own Harness ID, which no registered harness answers to, is disclosed before submit
 * and after it, yet still registers. Every agent has a kind: Generic is chosen, not assumed.
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
  const generic = page.getByRole("radio", { name: /^Generic/ });
  const before = await generic.boundingBox();
  await generic.click();
  // The click is also the Name field's blur, so its message appears: the cards must not move.
  await expect(page.getByText("Name is required.")).toBeVisible();
  expect(await generic.boundingBox()).toEqual(before);
  await expect(generic).toHaveAttribute("aria-checked", "true");
  await expect(page.getByLabel("Harness ID")).toBeEditable();
  await expect(page.getByLabel("Harness ID")).toHaveValue("generic");
});

test("nothing is chosen for a new agent, and Register says what is missing", async ({ page }) => {
  await openRegisterPage(page);
  for (const radio of await page.getByRole("radiogroup", { name: "Agent harness" }).getByRole("radio").all()) {
    await expect(radio).toHaveAttribute("aria-checked", "false");
  }
  const submit = page.getByRole("button", { name: "Register agent" });
  await page.getByPlaceholder("scout").fill("e2e-unsaved");
  await expect(submit).toBeDisabled();
  await expect(submit).toHaveAccessibleDescription("Choose a harness to register.");
  await expect(page.getByText("Choose a harness. Pick Generic for any other CLI agent.")).toBeVisible();

  // The read-only ID only echoes the card: a click must not draw the amber focus ring on it.
  const id = page.getByLabel("Harness ID");
  await id.click();
  expect(await id.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe("none");

  await page.getByRole("radio", { name: /^Codex CLI/ }).click();
  await expect(submit).toBeEnabled();
  await id.click();
  expect(await id.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe("none");
});

test("a near-miss Harness ID is flagged and one click fixes it", async ({ page }) => {
  await openRegisterPage(page);
  await page.getByRole("radio", { name: /^Generic/ }).click();
  await page.getByLabel("Harness ID").fill("openhand");

  const warning = kindWarning(page);
  await expect(warning).toContainText("“openhand” isn’t a registered harness. Did you mean OpenHands?");
  await expect(warning).toContainText("Runs may replay with the generic adapter.");
  // The live region announces the message only; the button that fixes it sits beside it.
  await expect(warning.getByRole("button")).toHaveCount(0);

  await page.getByRole("button", { name: "Use OpenHands" }).click();
  await expect(page.getByRole("radio", { name: /^OpenHands/ })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByLabel("Harness ID")).toHaveValue("openhands");
  await expect(warning).toHaveCount(0);
});

test("your own Harness ID still registers, and both the server and its edit page say so", async ({ page }) => {
  const name = `e2e-custom-${Date.now()}`;
  await openRegisterPage(page);
  await page.getByPlaceholder("scout").fill(name);
  await page.getByRole("radio", { name: /^Generic/ }).click();
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
  // The server's own warning follows the operator to the next page.
  await expect(page.getByText(`Registered “${name}” with a warning`)).toBeVisible();
  await expect(page.getByText(body.warnings[0])).toBeVisible();

  await page.goto(`/ui/agents/new/?agent=${encodeURIComponent(name)}`);
  await expect(page.getByRole("heading", { name: `Edit “${name}”` })).toBeVisible();
  await expect(kindWarning(page)).toContainText("“my-agent-cli” isn’t a registered harness.");
});

test("a generic agent saves as generic and reads as Generic on its pages", async ({ page }) => {
  const name = `e2e-generic-${Date.now()}`;
  await openRegisterPage(page);
  await page.getByPlaceholder("scout").fill(name);
  await page.getByRole("radio", { name: /^Generic/ }).click();
  // Best effort: which spans an arbitrary agent exports is unknown, never counted supported.
  await expect(page.getByText("Generic adapter · best effort")).toBeVisible();
  await expect(page.getByText("9 unknown")).toBeVisible();

  const saved = page.waitForResponse(
    (r) => r.url().endsWith("/api/agents") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Register agent" }).click();
  const response = await saved;
  created.push(name);
  expect(await response.json()).toMatchObject({ kind: "generic", warnings: [] });

  await page.goto(`/ui/agents/detail/?name=${encodeURIComponent(name)}`);
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByText(/\bCustom\b/)).toHaveCount(0);
  await expect(page.getByText("Generic", { exact: true }).first()).toBeVisible();

  await page.goto(`/ui/agents/new/?agent=${encodeURIComponent(name)}`);
  await expect(page.getByRole("radio", { name: /^Generic/ })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByLabel("Harness ID")).toHaveValue("generic");
  // Edit mode: the rename note sits in the Name line itself, not under an empty one.
  const field = await page.getByLabel(/^Name/).boundingBox();
  const note = await page.getByText("Renaming keeps the agent’s history — runs stay attached.").boundingBox();
  expect(note!.y - (field!.y + field!.height)).toBeLessThanOrEqual(6);
});
