import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";

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

import { RunList } from "./run-list";

describe("graceful degradation", () => {
  it("renders a clear error (never crashes) when the server returns 500", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderWithProviders(<RunList />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn’t load runs/i)).toBeInTheDocument(),
    );
  });

  it("renders a loading state while the server is slow", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.get("*/api/runs", async () => {
        await gate;
        return HttpResponse.json([]);
      }),
    );
    renderWithProviders(<RunList />);
    expect(
      await screen.findByRole("status", { name: "Loading runs…" }),
    ).toBeInTheDocument();
    release();
  });
});
