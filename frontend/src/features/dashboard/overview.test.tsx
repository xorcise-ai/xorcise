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

import { Overview } from "./overview";

const SYSTEM = {
  role: "all",
  planes: [{ name: "rest", ok: true, detail: "ok" }],
  db_schema: "head",
  catalog: { state: "connected", message: null, last_sync: null },
  topology: "local",
};

function baseHandlers() {
  server.use(
    http.get("*/api/system", () => HttpResponse.json(SYSTEM)),
    http.get("*/api/agents", () => HttpResponse.json([])),
    http.get("*/api/missions", () => HttpResponse.json([])),
    http.get("*/api/runs", () => HttpResponse.json([])),
  );
}

describe("Overview system health", () => {
  it("shows a neutral 'checking' state during load, not a red down state", () => {
    baseHandlers();
    renderWithProviders(<Overview />);
    // First paint: system.data is undefined — must read as checking, not down.
    expect(screen.getAllByText(/checking/i).length).toBeGreaterThan(0);
  });

  it("reflects the real system status once it resolves", async () => {
    baseHandlers();
    renderWithProviders(<Overview />);
    // §4: all modules up reads as "Healthy" rather than "1/1 ok".
    await waitFor(() =>
      expect(screen.getByText(/healthy/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/checking/i)).toBeNull();
  });
});
