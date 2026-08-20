import { test, expect } from "@playwright/test";

/**
 * The console must not overflow its own pane at any width it ships to.
 *
 * This exists because a whole class of layout bug had gone unmeasured. Tailwind's `grid`
 * sets display and nothing else, so twenty-one grids whose only `grid-cols-*` utilities
 * carried a breakpoint prefix had NO column template below that breakpoint: the implicit
 * column sized to max-content, and every card route pushed a 559px card through a 295px
 * pane on a 375px viewport. It never produced a page-level scrollbar — the shell's own
 * `overflow-auto` swallowed it — so nothing looked wrong until something measured.
 *
 * Deliberately conservative: geometry only, no seeded state. It is written to pass against
 * a FRESH install, like the rest of this suite, so the routes it walks are the ones that
 * render something real with an empty database.
 */

const VIEWPORTS = [
  { name: "mobile", width: 375, height: 812 },
  { name: "tablet", width: 768, height: 1024 },
] as const;

const ROUTES = [
  { name: "dashboard", path: "/ui/" },
  { name: "missions", path: "/ui/missions/" },
  { name: "runs", path: "/ui/runs/" },
  { name: "agents", path: "/ui/agents/" },
  { name: "settings", path: "/ui/settings/" },
  { name: "setup", path: "/ui/setup/" },
] as const;

/** Elements sticking out past the right edge, ignoring anything deliberately pinned. */
const overflowing = () => {
  const vw = document.documentElement.clientWidth;
  const out: string[] = [];
  for (const el of Array.from(document.querySelectorAll("body *"))) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const cs = getComputedStyle(el);
    if (cs.position === "fixed" || cs.display === "none" || cs.visibility === "hidden") continue;
    // A container that scrolls horizontally ON PURPOSE (a wide table, a code block) is
    // allowed to hold content wider than itself; the failure is content escaping a pane
    // that has no way to reveal it.
    if (r.right > vw + 1) {
      let scrollable = false;
      for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
        const ox = getComputedStyle(n).overflowX;
        if (ox === "auto" || ox === "scroll") {
          scrollable = n.scrollWidth > n.clientWidth + 1 && n.getBoundingClientRect().right <= vw + 1;
          break;
        }
      }
      if (!scrollable) {
        const cls = typeof el.className === "string" ? el.className.trim().split(/\s+/).slice(0, 4).join(".") : "";
        out.push(`${el.tagName.toLowerCase()}${cls ? "." + cls : ""} +${Math.round(r.right - vw)}px`);
      }
    }
  }
  return Array.from(new Set(out)).slice(0, 8);
};

for (const vp of VIEWPORTS) {
  test.describe(`${vp.name} (${vp.width}px)`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    for (const route of ROUTES) {
      test(`${route.name} fits its pane`, async ({ page }) => {
        await page.goto(route.path);
        // The shell renders before data arrives; wait for the chrome, not the content,
        // so an empty database cannot make this flake.
        await page.waitForSelector("main", { state: "attached" });
        await page.waitForLoadState("networkidle");

        const pageScroll = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(pageScroll, "the document itself must never scroll sideways").toBeLessThanOrEqual(1);

        expect(await page.evaluate(overflowing)).toEqual([]);
      });
    }
  });
}
