import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { useUiStore } from "@/stores/ui";

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

import { AppShell } from "./app-shell";

beforeEach(() => useUiStore.setState({ sidebarCollapsed: false }));

describe("AppShell", () => {
  it("renders the wordmark, all primary nav, and the content", () => {
    renderWithProviders(
      <AppShell>
        <div>page-content</div>
      </AppShell>,
    );
    expect(screen.getByLabelText("XORCISE.AI")).toBeInTheDocument();
    for (const label of ["Dashboard", "Agents", "Missions", "Runs", "Results"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("page-content")).toBeInTheDocument();
  });

  it("collapsing the sidebar hides the nav labels visually but keeps them readable", () => {
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    );
    fireEvent.click(screen.getByLabelText("Toggle sidebar"));
    expect(useUiStore.getState().sidebarCollapsed).toBe(true);

    // The label is hidden visually, NOT removed. Removing it left the collapsed
    // link with no accessible name but `title`, which assistive tech may skip and
    // touch users cannot hover — so the rail was unlabelled in practice.
    const label = screen.getByText("Agents");
    expect(label).toHaveClass("sr-only");
    // …and the link still resolves by its accessible name.
    expect(screen.getByRole("link", { name: "Agents" })).toBeInTheDocument();
  });

  it("shows the server-unreachable banner when health fails", async () => {
    server.use(
      http.get("*/api/health", () => HttpResponse.json({}, { status: 503 })),
    );
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    );
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/Can't reach the XORCISE server/),
    );
  });
});
