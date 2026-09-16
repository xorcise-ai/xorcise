import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import type { ReactElement } from "react";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { makeQueryClient } from "@/lib/api/query-client";
import { missionFixture } from "@/test/fixtures";
import type { Announcement } from "@/lib/api/types";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { AppShell } from "@/components/layout/app-shell";
import { MissionCatalog } from "@/features/missions/catalog";

/**
 * The no-polling contract — the executable form of the product requirement.
 *
 * A published or withdrawn announcement becomes visible to somebody already using the app
 * ONLY when they deliberately refresh the browser page. That is a product decision, not a
 * performance one: a banner that could appear, change or disappear under a reader mid-task
 * would rewrite the page around them for reasons they did not cause and cannot see. The whole
 * design of `useAnnouncements` — `staleTime: Infinity`, every refetch knob pinned off, no
 * retry — exists to satisfy this test, and this test is the only thing that will notice if a
 * library upgrade, a copied-and-pasted hook or a well-meaning "make it live" change breaks it.
 *
 * So the assertion is a request COUNTER, not a spy on options: options can be written
 * correctly and still be overridden, and only counting the wire proves the contract. The
 * counter idiom is the one run-notifications.test.tsx uses — a closure counter inside a
 * `server.use(...)` handler.
 *
 * The harness mounts AppShell wrapping MissionCatalog, so BOTH placements consume the query
 * at once (the application banner in the shell, the catalog banner in the Remote tab), which
 * is also what proves "one request serves both placements".
 */

const announcements: Announcement[] = [
  {
    id: "app-1",
    revision: 1,
    placement: "application",
    tone: "information",
    body_md: "Scheduled maintenance on Friday.",
    dismissible: true,
  },
  {
    id: "cat-1",
    revision: 1,
    placement: "catalog",
    tone: "maintenance",
    body_md: "Pulls are slower than usual.",
    dismissible: true,
  },
];

function serveCatalog() {
  server.use(
    http.get("*/api/missions", () =>
      HttpResponse.json([missionFixture({ mission_id: "m1", name: "Stack Smash", source: "library" })]),
    ),
    http.get("*/api/missions/pull-jobs", () => HttpResponse.json(null)),
  );
}

const bothPlacements = (
  <AppShell>
    <MissionCatalog />
  </AppShell>
);

function mountBothPlacements() {
  serveCatalog();
  return renderWithProviders(bothPlacements);
}

/**
 * The same tree, but behind the app's REAL QueryClient rather than the test harness's.
 *
 * This matters for exactly one assertion. `src/test/render.tsx` builds its client with
 * `retry: false` already, so a no-retry test rendered through it passes whether or not the
 * hook overrides anything — the line it exists to protect would be unprotected, and deleting
 * `retry: false` from the hook would leave the whole suite green while production issued two
 * requests per document load for every 5xx or offline `/api/announcements`. Rendering through
 * `makeQueryClient()` puts the hook's own override back in the load-bearing position, and
 * does it against the real defaults rather than a hand-copied `retry: 1`, so the test also
 * notices if those defaults move.
 *
 * `retryDelay: 0` is the only thing overridden: a retry that WOULD happen then happens on the
 * next macrotask instead of a second later, so the test can prove its absence quickly.
 */
function renderWithProductionClient(ui: ReactElement) {
  const client = makeQueryClient();
  const defaults = client.getDefaultOptions();
  client.setDefaultOptions({ ...defaults, queries: { ...defaults.queries, retryDelay: 0 } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** Let any retry the query layer scheduled actually run before counting. */
async function flushRetries() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });
}

describe("the announcements query fires exactly once per document load", () => {
  it("survives focus, reconnect, remount and client-side navigation without a second request", async () => {
    let calls = 0;
    server.use(
      http.get("*/api/announcements", () => {
        calls += 1;
        return HttpResponse.json({ announcements });
      }),
    );

    const { rerender } = mountBothPlacements();

    // The application banner proves the single request resolved and reached the shell.
    expect(
      await screen.findByTestId("announcement-banner-application"),
    ).toBeInTheDocument();
    expect(calls).toBe(1);

    // Window focus — the classic "refetch when the user comes back" trigger. TanStack Query
    // watches both the window focus event and document visibility, so both are dispatched.
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
    });

    // Reconnect — the other automatic trigger, fired when the browser regains the network.
    await act(async () => {
      window.dispatchEvent(new Event("online"));
    });

    // A re-render of the whole tree (the same QueryClient, remounted consumers).
    rerender(
      <AppShell>
        <MissionCatalog />
      </AppShell>,
    );

    // Client-side navigation within the catalog: switching tabs unmounts and remounts the
    // catalog placement, which is the in-app equivalent of routing away and back.
    fireEvent.click(await screen.findByRole("tab", { name: /XORCISE Remote/i }));
    expect(
      await screen.findByTestId("announcement-banner-catalog"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Your Own/i }));
    expect(screen.queryByTestId("announcement-banner-catalog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /XORCISE Remote/i }));
    expect(
      await screen.findByTestId("announcement-banner-catalog"),
    ).toBeInTheDocument();

    // Both placements are on screen, served by that one request.
    expect(screen.getByTestId("announcement-banner-application")).toBeInTheDocument();
    expect(screen.getByTestId("announcement-banner-catalog")).toBeInTheDocument();

    // Settle any microtask a refetch would have been queued on, then count.
    await waitFor(() => expect(calls).toBe(1));
    expect(calls).toBe(1);
  });

  it("does not retry a failing endpoint, against the app's real QueryClient", async () => {
    // `retry: false` is part of the same contract, not error handling: the backend answers
    // 200 with an empty list for every failure it can absorb, so a non-200 is not a condition
    // a retry improves — it is just a second request.
    //
    // Rendered through `makeQueryClient()`, the client the app actually ships, because that
    // is the one that sets `retry: 1`. The test harness's client sets `retry: false` itself,
    // so this case run through `renderWithProviders` would pass with the hook's override
    // deleted — see the note on `renderWithProductionClient`.
    let calls = 0;
    server.use(
      http.get("*/api/announcements", () => {
        calls += 1;
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );

    serveCatalog();
    renderWithProductionClient(bothPlacements);

    // The catalog renders, so the page is alive; the banner simply never appears.
    expect(await screen.findByRole("tab", { name: /XORCISE Remote/i })).toBeInTheDocument();
    await waitFor(() => expect(calls).toBe(1));

    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("online"));
    });

    // The retry the default would have scheduled is due by now if it was ever scheduled.
    await flushRetries();

    expect(calls).toBe(1);
    expect(screen.queryByTestId("announcement-banner-application")).not.toBeInTheDocument();
    expect(screen.queryByTestId("announcement-banner-catalog")).not.toBeInTheDocument();
  });
});
