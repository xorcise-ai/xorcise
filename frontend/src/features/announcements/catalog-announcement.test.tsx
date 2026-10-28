import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { DISMISSED_STORAGE_KEY } from "./dismissal";
import type { Announcement } from "@/lib/api/types";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import { CatalogAnnouncement } from "./catalog-announcement";

/**
 * The catalog placement, pinned separately from the application one.
 *
 * The two components are near-identical by design and that is exactly why both are tested:
 * the dismissal rules below were wrong in both files in the same way, and a suite that
 * covered only the shell banner would have proved half the fix.
 *
 * Rendered inside a stand-in app shell — `<main id="main-content" tabIndex={-1}>` — because
 * the real one is what the focus handoff targets, and this component normally renders deep
 * inside it on the Mission Catalog's XORCISE Remote tab.
 */
const CATALOG: Announcement = {
  id: "cat-1",
  revision: 1,
  placement: "catalog",
  tone: "information",
  body_md: "Three new missions landed.",
  dismissible: true,
};

function serveAnnouncements(...announcements: Announcement[]) {
  server.use(
    http.get("*/api/announcements", () => HttpResponse.json({ announcements })),
  );
}

function renderInShell() {
  return renderWithProviders(
    <main id="main-content" tabIndex={-1}>
      <CatalogAnnouncement />
      <div>mission-grid</div>
    </main>,
  );
}

describe("CatalogAnnouncement", () => {
  it("renders the catalog banner and dismisses it", async () => {
    serveAnnouncements(CATALOG);
    renderInShell();

    expect(await screen.findByTestId("announcement-banner-catalog")).toHaveTextContent(
      "Three new missions landed.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Dismiss announcement" }));
    expect(screen.queryByTestId("announcement-banner-catalog")).not.toBeInTheDocument();
  });

  it("ignores an application-placement announcement", async () => {
    serveAnnouncements({ ...CATALOG, id: "app-1", placement: "application" });
    renderInShell();

    await waitFor(() => expect(screen.getByText("mission-grid")).toBeInTheDocument());
    expect(screen.queryByTestId("announcement-banner-catalog")).not.toBeInTheDocument();
  });

  it("still shows an undismissible banner that storage claims was dismissed", async () => {
    // Same rule as the shell placement: the dismissal record is the reader's own file, so it
    // may hide only what the publisher marked dismissible. An incident is forced
    // undismissible upstream precisely so it cannot be hidden.
    const incident: Announcement = {
      ...CATALOG,
      id: "inc-2",
      tone: "incident",
      dismissible: false,
      body_md: "Remote pulls are failing.",
    };
    window.localStorage.setItem(DISMISSED_STORAGE_KEY, JSON.stringify({ "inc-2": 1 }));
    serveAnnouncements(incident);
    renderInShell();

    expect(await screen.findByTestId("announcement-banner-catalog")).toHaveTextContent(
      "Remote pulls are failing.",
    );
    expect(
      screen.queryByRole("button", { name: "Dismiss announcement" }),
    ).not.toBeInTheDocument();
  });

  it("hands focus to the main landmark when the close button unmounts", async () => {
    serveAnnouncements(CATALOG);
    renderInShell();

    const close = await screen.findByRole("button", { name: "Dismiss announcement" });
    close.focus();
    fireEvent.click(close);

    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(screen.getByRole("main"));
  });
});
