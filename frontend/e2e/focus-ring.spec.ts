import { test, expect, type Locator } from "@playwright/test";

/**
 * One focus indicator per control (#182).
 *
 * The global :focus-visible outline in globals.css is a fallback for elements that set no focus
 * style of their own. It used to sit outside any CSS layer, and unlayered CSS beats Tailwind's
 * `@layer utilities` whatever the specificity, so every `focus-visible:outline-none` was ignored
 * and a focused Button drew its own ring AND the amber outline. jsdom has no stylesheet, so only a
 * browser can see this.
 */

async function keyboardFocus(target: Locator) {
  // A key press first puts Chromium in keyboard modality, so the focus() below matches
  // :focus-visible the way a Tab would.
  await target.page().keyboard.press("Shift");
  await target.focus();
}

test("a focused Button shows its own ring and no outline", async ({ page }) => {
  await page.goto("/ui/agents/");
  const register = page.getByRole("button", { name: "Register agent" }).first();
  await keyboardFocus(register);
  const style = await register.evaluate((el) => {
    const cs = getComputedStyle(el);
    return { outline: cs.outlineStyle, ring: cs.boxShadow };
  });
  expect(style.outline).toBe("none");
  expect(style.ring).not.toBe("none");
});

test("an element with no focus style of its own still gets the outline", async ({ page }) => {
  await page.goto("/ui/agents/");
  await page.getByRole("button", { name: "Register agent" }).first().waitFor();
  await page.evaluate(() => {
    const probe = document.createElement("a");
    probe.href = "#";
    probe.id = "focus-probe";
    probe.textContent = "probe";
    document.body.prepend(probe);
  });
  const probe = page.locator("#focus-probe");
  await keyboardFocus(probe);
  expect(await probe.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe("solid");
});
