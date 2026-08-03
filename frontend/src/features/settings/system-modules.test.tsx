import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { SettingsView } from "./settings-view";

const CONFIG = {
  judge: { configured: false, base_url: null, model_name: null, key_hint: null },
  default_budget_seconds: 3600,
  catalog: { connected: true, url: "https://catalog.example.com" },
  network: { headscale_url: null, advertise_host: null },
};

const SYSTEM = {
  role: "all",
  planes: [
    { name: "rest", ok: true, detail: "ok", location: "127.0.0.1:3001" },
    { name: "docker", ok: false, detail: "missing", location: "local daemon" },
  ],
  db_schema: "head",
  catalog: { state: "connected", message: null, last_sync: null },
  remotes: [],
  home: "/home/u/.xorcise",
  db_url: "sqlite:////home/u/.xorcise/xorcise.db",
  topology: "local",
};

describe("Settings — Environment + Modules", () => {
  it("shows home, topology, DB state and per-module locations", async () => {
    server.use(
      http.get("*/api/config", () => HttpResponse.json(CONFIG)),
      http.get("*/api/system", () => HttpResponse.json(SYSTEM)),
    );
    renderWithProviders(<SettingsView />);

    expect(await screen.findByText("/home/u/.xorcise")).toBeInTheDocument();
    expect(screen.getByText("up to date")).toBeInTheDocument();
    // Module rows with locations (name is lowercase text, CSS-uppercased).
    expect(screen.getByText("rest")).toBeInTheDocument();
    expect(screen.getByText("127.0.0.1:3001")).toBeInTheDocument();
    expect(screen.getByText("local daemon")).toBeInTheDocument();
  });

  it("names distributed deployment but offers NO editor for its addresses", async () => {
    // The editor was withdrawn on purpose: it exposed two fields — Headscale URL and advertise
    // host — for a deployment model that needs many more, so it read as a finished feature; and
    // filling them in points a local-only server at a control plane that isn't there, which
    // breaks `xorcise up`. `xorcise config set-network` remains, labelled EXPERIMENTAL.
    //
    // The CARD is back as a preview, which is a different claim: it says the capability is
    // coming and shows its shape. This test is the guard on the part that mattered — the card
    // must stay unable to set anything, so re-adding a field fails here rather than in the
    // field.
    server.use(
      http.get("*/api/config", () => HttpResponse.json(CONFIG)),
      http.get("*/api/system", () => HttpResponse.json(SYSTEM)),
    );
    renderWithProviders(<SettingsView />);
    await screen.findByText("/home/u/.xorcise");

    expect(screen.getByText("Distributed deployment")).toBeInTheDocument();
    // The preview's own headline — "Coming soon" alone is ambiguous, the Connection card
    // carries that phrase too for multi-user auth.
    expect(screen.getByText("One XORCISE, many machines.")).toBeInTheDocument();

    expect(screen.queryByPlaceholderText(/headscale\.example/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /deploy remotely/i })).not.toBeInTheDocument();
    // And no field labels for them either, while they are unset: an empty "— (local)" row is
    // noise on the single-host install everyone actually runs.
    expect(screen.queryByText("Headscale URL")).not.toBeInTheDocument();
    expect(screen.queryByText("Advertise host")).not.toBeInTheDocument();
  });

  it("diagnoses a remote Headscale from the PROBE, not from a config echo", async () => {
    // Withdrawing the editor must not withdraw the DIAGNOSIS. It does not, and the reason
    // matters: the Modules card reports the address the headscale probe actually talked to and
    // whether it answered. Echoing config instead would be strictly worse — `xorcise up` WRITES
    // headscale_url for the Headscale it provisions locally, so "a value is set" cannot tell a
    // healthy single-host install from an operator-chosen remote (the CLI's own
    // `bool(headscale_url)` test made exactly that mistake).
    server.use(
      http.get("*/api/config", () =>
        HttpResponse.json({
          ...CONFIG,
          network: { headscale_url: "https://hs.remote:8080", advertise_host: "10.0.0.5" },
        }),
      ),
      http.get("*/api/system", () =>
        HttpResponse.json({
          ...SYSTEM,
          planes: [
            ...SYSTEM.planes,
            {
              name: "headscale",
              role: "headscale",
              label: "Headscale",
              state: "down",
              ok: false,
              detail: "unreachable",
              location: "https://hs.remote:8080",
            },
          ],
        }),
      ),
    );
    renderWithProviders(<SettingsView />);

    // The offending address, and the verdict on it.
    expect(await screen.findByText("https://hs.remote:8080")).toBeInTheDocument();
    expect(screen.getByText("unreachable")).toBeInTheDocument();
    // Still nothing here that could have set it.
    expect(screen.queryByPlaceholderText(/headscale\.example/i)).not.toBeInTheDocument();
  });
});
