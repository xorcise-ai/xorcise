import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor, act } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { useUiStore } from "@/stores/ui";
import type { Announcement } from "@/lib/api/types";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
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

beforeEach(() => useUiStore.setState({ sidebarCollapsed: false }));

const APPLICATION: Announcement = {
  id: "app-1",
  revision: 1,
  placement: "application",
  tone: "information",
  body_md: "Scheduled maintenance on Friday.",
  dismissible: true,
};

function serveAnnouncements(...announcements: Announcement[]) {
  server.use(
    http.get("*/api/announcements", () => HttpResponse.json({ announcements })),
  );
}

function renderShell() {
  return renderWithProviders(
    <AppShell>
      <div>page-content</div>
    </AppShell>,
  );
}

describe("ApplicationAnnouncement in the app shell", () => {
  it("appears below the header and above the sidebar/main row", async () => {
    serveAnnouncements(APPLICATION);
    renderShell();

    const banner = await screen.findByTestId("announcement-banner-application");
    expect(banner).toHaveTextContent("Scheduled maintenance on Friday.");

    // Placement, asserted structurally rather than by class: the banner must be a SIBLING
    // after the header and before the row holding the sidebar and <main>. Inside <main> it
    // would scroll away with the page (that element is overflow-auto and the Page primitive
    // inside it assumes h-full), and it must never overlay the content.
    const main = screen.getByRole("main");
    expect(main).not.toContainElement(banner);
    expect(banner.parentElement).toBe(main.closest(".flex.h-dvh"));
    expect(
      banner.compareDocumentPosition(main) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByText("page-content")).toBeInTheDocument();
  });

  it("renders nothing at all when the list is empty", async () => {
    serveAnnouncements();
    renderShell();

    // The default MSW handler already serves an empty list; this asserts the app is not
    // merely un-rendered but settled, and that no skeleton or placeholder took its place.
    await waitFor(() => expect(screen.getByText("page-content")).toBeInTheDocument());
    expect(screen.queryByTestId("announcement-banner-application")).not.toBeInTheDocument();
    expect(screen.queryByText(/loading/i)).not.toBeInTheDocument();
  });

  it("renders nothing when the endpoint 500s — a failure is not an error to show", async () => {
    server.use(
      http.get("*/api/announcements", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderShell();

    await waitFor(() => expect(screen.getByText("page-content")).toBeInTheDocument());
    expect(screen.queryByTestId("announcement-banner-application")).not.toBeInTheDocument();
    // …and the layout is otherwise untouched: the shell's own unreachable banner, which is a
    // different signal about a different service, must not be implicated.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stays dismissed across a fresh QueryClient over the same storage", async () => {
    serveAnnouncements(APPLICATION);
    const first = renderShell();

    await screen.findByTestId("announcement-banner-application");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss announcement" }));
    expect(screen.queryByTestId("announcement-banner-application")).not.toBeInTheDocument();
    first.unmount();

    // A new document load: a brand-new QueryClient, the same localStorage. This is the only
    // moment the app re-reads announcements at all, so it is the only moment the dismissal
    // has to survive — and it does, because it is in storage and not in the query cache.
    renderShell();
    await waitFor(() => expect(screen.getByText("page-content")).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.queryByTestId("announcement-banner-application")).not.toBeInTheDocument(),
    );
  });

  it("comes back when the publisher bumps the revision", async () => {
    serveAnnouncements(APPLICATION);
    const first = renderShell();

    await screen.findByTestId("announcement-banner-application");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss announcement" }));
    first.unmount();

    // The publisher edited the banner. The reader dismissed revision 1; they have not seen
    // revision 2, so they get it.
    serveAnnouncements({
      ...APPLICATION,
      revision: 2,
      body_md: "Maintenance moved to Saturday.",
    });
    renderShell();
    expect(await screen.findByTestId("announcement-banner-application")).toHaveTextContent(
      "Maintenance moved to Saturday.",
    );
  });

  it("survives a 200 whose JSON has no announcements key", async () => {
    // The generated type says the key is always there; what actually answers on /api can be a
    // canned fixture, a proxy or a mismatched build. This banner lives in the root layout, so
    // an unguarded `.find` on a missing key would throw there and take down every route in
    // the app — a whole dead UI in exchange for one absent decoration.
    //
    // Counted and awaited rather than just rendered: "page-content" is on screen before the
    // announcements request even resolves, so asserting on it proves nothing about what the
    // response does. The failure this guards happens when the response is COMMITTED, so the
    // test has to get past that commit before it is allowed to conclude anything.
    let calls = 0;
    server.use(
      http.get("*/api/announcements", () => {
        calls += 1;
        return HttpResponse.json({});
      }),
    );
    renderShell();

    await waitFor(() => expect(calls).toBe(1));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.queryByTestId("announcement-banner-application")).not.toBeInTheDocument();
    // The rest of the shell is intact — this is the "app still loads" assertion.
    expect(screen.getByText("page-content")).toBeInTheDocument();
    expect(screen.getByLabelText("XORCISE.AI")).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
  });

  it("ignores a catalog-placement announcement", async () => {
    serveAnnouncements({
      ...APPLICATION,
      id: "cat-1",
      placement: "catalog",
      body_md: "Catalog notice.",
    });
    renderShell();

    await waitFor(() => expect(screen.getByText("page-content")).toBeInTheDocument());
    expect(screen.queryByTestId("announcement-banner-application")).not.toBeInTheDocument();
    expect(screen.queryByText("Catalog notice.")).not.toBeInTheDocument();
  });
});
