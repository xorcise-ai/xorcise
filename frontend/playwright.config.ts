import { defineConfig } from "@playwright/test";

// E2E runs against a SERVED operator surface (the server serving the static
// export at /ui). CI/manual: `xorcise up` (or `xorcise serve`) + `npx playwright
// install`, then `npm run test:e2e`. Base URL is overridable for a remote server.
//
// baseURL is the ORIGIN ONLY, and specs carry the `/ui` mount themselves. It used to
// include the mount — `http://localhost:3001/ui` — which silently broke every
// navigation: a leading-slash path like `goto("/")` is ORIGIN-relative, so it drops
// the base's path and lands on `http://localhost:3001/`, which the server 404s. The
// suite had been failing on that for months without anyone noticing, because nothing
// ran it. Keeping the mount in the specs makes the URL under test visible at the call
// site and removes the trap entirely.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  reporter: "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3001",
    trace: "on-first-retry",
  },
});
