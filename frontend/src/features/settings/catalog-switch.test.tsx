import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { SettingsView } from "./settings-view";

function configView(connected: boolean) {
  return {
    judge: {
      configured: false,
      base_url: null,
      model_name: null,
      key_hint: null,
      timeout_seconds: 120,
    },
    default_budget_seconds: 600,
    catalog: { connected, url: "https://catalog.example.com" },
  };
}

describe("Settings — XORCISE Remote switch", () => {
  it("shows connection state and toggles the remote off via PUT /config/catalog", async () => {
    let putBody: { connected: boolean } | null = null;
    let connected = true;
    server.use(
      http.get("*/api/config", () => HttpResponse.json(configView(connected))),
      http.get("*/api/system", () =>
        HttpResponse.json({
          role: "all",
          planes: [],
          db_schema: "head",
          catalog: { state: "connected" },
          remotes: [],
        }),
      ),
      http.put("*/api/config/catalog", async ({ request }) => {
        putBody = (await request.json()) as { connected: boolean };
        connected = putBody.connected;
        return HttpResponse.json(configView(connected));
      }),
    );

    renderWithProviders(<SettingsView />);

    await waitFor(() =>
      expect(screen.getByText("XORCISE Remote")).toBeInTheDocument(),
    );
    const sw = await screen.findByRole("switch", {
      name: /connect xorcise remote catalog/i,
    });
    expect(sw).toHaveAttribute("aria-checked", "true");

    fireEvent.click(sw);
    await waitFor(() => expect(putBody).toEqual({ connected: false }));
    await waitFor(() =>
      expect(screen.getByText("Disconnected")).toBeInTheDocument(),
    );
  });
});
