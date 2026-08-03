import { describe, it, expect } from "vitest";
import { screen, fireEvent, waitFor, within } from "@testing-library/react";
import { http, HttpResponse, delay } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { useUiStore } from "@/stores/ui";
import { SettingsView } from "./settings-view";

const TERRAIN_DEFAULT = {
  configured: true,
  uses_judge_default: true,
  base_url: null,
  model_name: "judge-m",
  key_hint: "…jk",
};

const UNCONFIGURED = {
  judge: { configured: false, base_url: null, model_name: null, key_hint: null },
  terrain: TERRAIN_DEFAULT,
  default_budget_seconds: 3600,
};
const SYSTEM = {
  role: "all",
  planes: [{ name: "rest", ok: true, detail: "ok" }],
  db_schema: "head",
  catalog: { state: "disconnected", message: null, last_sync: null },
  remotes: [],
};

function mockSystem() {
  server.use(http.get("*/api/system", () => HttpResponse.json(SYSTEM)));
}

describe("SettingsView", () => {
  it("shows the judge as not configured", async () => {
    mockSystem();
    server.use(http.get("*/api/config", () => HttpResponse.json(UNCONFIGURED)));
    renderWithProviders(<SettingsView />);
    expect(await screen.findByText(/not configured/i)).toBeInTheDocument();
  });

  it("saves the model via PUT with the entered fields", async () => {
    mockSystem();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      http.get("*/api/config", () => HttpResponse.json(UNCONFIGURED)),
      http.put("*/api/config/model", async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          judge: {
            configured: true,
            base_url: null,
            model_name: "gpt-4o-mini",
            key_hint: "…1234",
          },
          terrain: TERRAIN_DEFAULT,
          default_budget_seconds: 3600,
        });
      }),
      // Save now auto-runs the live test — must be handled.
      http.post("*/api/config/model/test", () =>
        HttpResponse.json({ ok: true, status: "ok", message: null, model_name: "gpt-4o-mini" }),
      ),
    );
    renderWithProviders(<SettingsView />);
    // Judge and Terrain both carry an "API key" / "Model name" / "Save" now, so scope to the card.
    const judge = (await screen.findByText("Judge model")).closest("section")!;
    // findBy for the first field: the card renders its h2 before config loads, so wait for the form.
    fireEvent.change(await within(judge).findByLabelText(/api key/i), {
      target: { value: "sk-secret-1234" },
    });
    fireEvent.change(within(judge).getByLabelText(/model name/i), {
      target: { value: "gpt-4o-mini" },
    });
    fireEvent.click(within(judge).getByRole("button", { name: /save/i }));
    await waitFor(() =>
      expect(putBody).toMatchObject({ key: "sk-secret-1234", model_name: "gpt-4o-mini" }),
    );
  });

  it("surfaces the judge transcript token limit and sends an edited value", async () => {
    mockSystem();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      http.get("*/api/config", () =>
        HttpResponse.json({
          judge: {
            configured: true,
            base_url: null,
            model_name: "gpt-4o-mini",
            key_hint: "…1234",
            transcript_max_tokens: 256000,
          },
          terrain: TERRAIN_DEFAULT,
          default_budget_seconds: 3600,
        }),
      ),
      http.put("*/api/config/model", async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          judge: {
            configured: true,
            base_url: null,
            model_name: "gpt-4o-mini",
            key_hint: "…1234",
            transcript_max_tokens: 128000,
          },
          terrain: TERRAIN_DEFAULT,
          default_budget_seconds: 3600,
        });
      }),
      http.post("*/api/config/model/test", () =>
        HttpResponse.json({ ok: true, status: "ok", message: null, model_name: "gpt-4o-mini" }),
      ),
    );
    renderWithProviders(<SettingsView />);
    // the current limit is surfaced (formatted) — scope to the judge card (the terrain override
    // shows its own token limit, defaulting to the same 256,000).
    const judge = (await screen.findByText("Judge model")).closest("section")!;
    expect(await within(judge).findByText("256,000")).toBeInTheDocument();
    fireEvent.change(within(judge).getByLabelText(/transcript token limit/i), {
      target: { value: "128000" },
    });
    fireEvent.click(within(judge).getByRole("button", { name: /save/i }));
    await waitFor(() => expect(putBody).toMatchObject({ transcript_max_tokens: 128000 }));
  });

  it("defaults the pre-flight transcript cap to off, and enabling it sends a positive value", async () => {
    mockSystem();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      // no transcript_max_tokens => cap off by default
      http.get("*/api/config", () =>
        HttpResponse.json({
          judge: { configured: true, base_url: null, model_name: "gpt-4o-mini", key_hint: "…1234" },
          terrain: TERRAIN_DEFAULT,
          default_budget_seconds: 3600,
        }),
      ),
      http.put("*/api/config/model", async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          judge: {
            configured: true,
            base_url: null,
            model_name: "gpt-4o-mini",
            key_hint: "…1234",
            transcript_max_tokens: 256000,
          },
          terrain: TERRAIN_DEFAULT,
          default_budget_seconds: 3600,
        });
      }),
      http.post("*/api/config/model/test", () =>
        HttpResponse.json({ ok: true, status: "ok", message: null, model_name: "gpt-4o-mini" }),
      ),
    );
    renderWithProviders(<SettingsView />);
    const judge = (await screen.findByText("Judge model")).closest("section")!;
    // off by default: the switch is unchecked and the slider isn't shown
    const sw = await within(judge).findByRole("switch", {
      name: /enable a pre-flight transcript/i,
    });
    expect(sw).toHaveAttribute("aria-checked", "false");
    expect(within(judge).queryByLabelText(/transcript token limit/i)).not.toBeInTheDocument();
    // enabling the cap flips the switch and reveals the slider…
    fireEvent.click(sw);
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(within(judge).getByLabelText(/transcript token limit/i)).toBeInTheDocument();
    // …and saving sends the positive cap value
    fireEvent.click(within(judge).getByRole("button", { name: /save/i }));
    await waitFor(() => expect(putBody?.transcript_max_tokens).toBeGreaterThan(0));
  });

  it("surfaces the per-span token cap and sends an edited value (0 disables)", async () => {
    mockSystem();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      http.get("*/api/config", () =>
        HttpResponse.json({
          judge: {
            configured: true,
            base_url: null,
            model_name: "gpt-4o-mini",
            key_hint: "…1234",
            span_max_tokens: 2000,
          },
          terrain: TERRAIN_DEFAULT,
          default_budget_seconds: 3600,
        }),
      ),
      http.put("*/api/config/model", async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          judge: {
            configured: true,
            base_url: null,
            model_name: "gpt-4o-mini",
            key_hint: "…1234",
            span_max_tokens: 0,
          },
          terrain: TERRAIN_DEFAULT,
          default_budget_seconds: 3600,
        });
      }),
      http.post("*/api/config/model/test", () =>
        HttpResponse.json({ ok: true, status: "ok", message: null, model_name: "gpt-4o-mini" }),
      ),
    );
    renderWithProviders(<SettingsView />);
    const input = await screen.findByLabelText(/per-span token cap/i);
    expect(input).toHaveValue(2000); // current cap surfaced
    // 0 is a valid value (disable the cap) — it must still be sent even though it's falsy.
    fireEvent.change(input, { target: { value: "0" } });
    // scope the Save click to the judge card (other cards render their own Save button).
    const judge = input.closest("section")!;
    fireEvent.click(within(judge).getByRole("button", { name: /save/i }));
    await waitFor(() => expect(putBody).toMatchObject({ span_max_tokens: 0 }));
  });

  it("auto-runs the live test after saving a key", async () => {
    mockSystem();
    let tested = false;
    server.use(
      http.get("*/api/config", () => HttpResponse.json(UNCONFIGURED)),
      http.put("*/api/config/model", () =>
        HttpResponse.json({
          judge: {
            configured: true,
            base_url: null,
            model_name: "gpt-4o-mini",
            key_hint: "…1234",
          },
          terrain: TERRAIN_DEFAULT,
          default_budget_seconds: 3600,
        }),
      ),
      http.post("*/api/config/model/test", () => {
        tested = true;
        return HttpResponse.json({
          ok: true,
          status: "ok",
          message: null,
          model_name: "gpt-4o-mini",
        });
      }),
    );
    renderWithProviders(<SettingsView />);
    const card = (await screen.findByText("Judge model")).closest("section")!;
    fireEvent.change(await within(card).findByLabelText(/api key/i), {
      target: { value: "sk-secret-1234" },
    });
    fireEvent.click(within(card).getByRole("button", { name: /save/i }));
    // The test fires automatically and the status line lands on Connected — no
    // extra click. Scope to the judge card ("Connected" also names the catalog
    // state and the card's own badge).
    await waitFor(() =>
      expect(within(card).getByRole("status")).toHaveTextContent("Connected"),
    );
    expect(tested).toBe(true);
  });

  const CONFIGURED = {
    judge: {
      configured: true,
      base_url: null,
      model_name: "gpt-4o-mini",
      key_hint: "…1234",
    },
    terrain: TERRAIN_DEFAULT,
    default_budget_seconds: 3600,
  };

  it("live-tests the judge key and shows Connected", async () => {
    mockSystem();
    server.use(
      http.get("*/api/config", () => HttpResponse.json(CONFIGURED)),
      http.post("*/api/config/model/test", () =>
        HttpResponse.json({
          ok: true,
          status: "ok",
          message: null,
          model_name: "gpt-4o-mini",
        }),
      ),
    );
    renderWithProviders(<SettingsView />);
    const card = (await screen.findByText("Judge model")).closest("section")!;
    fireEvent.click(await within(card).findByRole("button", { name: /^test$/i }));
    await waitFor(() =>
      expect(within(card).getByRole("status")).toHaveTextContent("Connected"),
    );
    // The header badge upgrades to the verified state too.
    expect(within(card).getAllByText("Connected")).toHaveLength(2);
  });

  it("surfaces the provider error when the judge key test fails", async () => {
    mockSystem();
    server.use(
      http.get("*/api/config", () => HttpResponse.json(CONFIGURED)),
      http.post("*/api/config/model/test", () =>
        HttpResponse.json({
          ok: false,
          status: "error",
          message: "401 Unauthorized",
          model_name: "gpt-4o-mini",
        }),
      ),
    );
    renderWithProviders(<SettingsView />);
    const judge = (await screen.findByText("Judge model")).closest("section")!;
    fireEvent.click(await within(judge).findByRole("button", { name: /^test$/i }));
    expect(await screen.findByText(/401 Unauthorized/i)).toBeInTheDocument();
  });

  it("shows the masked key preview and never the raw key when configured", async () => {
    mockSystem();
    server.use(
      http.get("*/api/config", () =>
        HttpResponse.json({
          judge: {
            configured: true,
            base_url: "https://api.openai.com/v1",
            model_name: "gpt-4o-mini",
            key_hint: "…1234",
          },
          terrain: TERRAIN_DEFAULT,
          default_budget_seconds: 3600,
        }),
      ),
    );
    renderWithProviders(<SettingsView />);
    expect(await screen.findByText(/…1234/)).toBeInTheDocument();
  });

  it("groups the cards into Evaluation, Library & Runtime, and Diagnostics regions (§6)", async () => {
    mockSystem();
    server.use(http.get("*/api/config", () => HttpResponse.json(UNCONFIGURED)));
    renderWithProviders(<SettingsView />);

    const evaluation = await screen.findByRole("region", { name: /evaluation/i });
    const runtime = screen.getByRole("region", { name: /mission library & runtime/i });
    const diagnostics = screen.getByRole("region", { name: /diagnostics/i });

    // Evaluation = the models that grade and attribute a run.
    expect(within(evaluation).getByText("Judge model")).toBeInTheDocument();
    expect(within(evaluation).getByText("Terrain model")).toBeInTheDocument();

    // Mission Library & Runtime = catalog source + environment.
    expect(within(runtime).getByText("XORCISE Remote")).toBeInTheDocument();
    expect(within(runtime).getByText("Environment")).toBeInTheDocument();

    // Diagnostics = read-only system health & connection, then the topology this host does
    // NOT have. Distributed deployment groups here — and last — so the preview reads as the
    // continuation of what Modules and Connection just reported, rather than sitting above
    // them as a control someone forgot to finish. It is a preview: it says the capability is
    // coming and sets nothing, because two address fields was never a deployment model and
    // filling them in breaks `xorcise up` on the single-host install.
    expect(within(diagnostics).getByText("Modules")).toBeInTheDocument();
    expect(within(diagnostics).getByText("Connection")).toBeInTheDocument();
    expect(within(diagnostics).getByText("Distributed deployment")).toBeInTheDocument();
    expect(within(runtime).queryByText("Distributed deployment")).not.toBeInTheDocument();
    expect(
      within(diagnostics).queryByPlaceholderText(/headscale\.example/i),
    ).not.toBeInTheDocument();
  });

  it("shows the terrain attribution model, defaulting to the judge config", async () => {
    mockSystem();
    server.use(http.get("*/api/config", () => HttpResponse.json(UNCONFIGURED)));
    renderWithProviders(<SettingsView />);
    expect(await screen.findByText(/terrain attribution model/i)).toBeInTheDocument();
    expect(screen.getByText(/judge-m/)).toBeInTheDocument();
    expect(screen.getByText(/using judge config/i)).toBeInTheDocument();
    // the override fields are shown directly now (no Advanced disclosure to expand)
    expect(screen.getByLabelText(/terrain model name/i)).toBeInTheDocument();
  });

  it("saving a terrain override sends the entered fields", async () => {
    mockSystem();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      http.get("*/api/config", () => HttpResponse.json(UNCONFIGURED)),
      http.put("*/api/config/terrain-model", async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          judge: { configured: false, base_url: null, model_name: null, key_hint: null },
          terrain: {
            configured: true,
            uses_judge_default: false,
            base_url: null,
            model_name: "claude-terrain",
            key_hint: null,
          },
          default_budget_seconds: 3600,
        });
      }),
      // Terrain save now auto-runs the live test — must be handled.
      http.post("*/api/config/terrain-model/test", () =>
        HttpResponse.json({ ok: true, status: "ok", message: null, model_name: "claude-terrain" }),
      ),
    );
    renderWithProviders(<SettingsView />);
    const terrainModelName = await screen.findByLabelText(/terrain model name/i);
    fireEvent.change(terrainModelName, { target: { value: "claude-terrain" } });
    const card = terrainModelName.closest("section")!;
    fireEvent.click(within(card).getByRole("button", { name: /^save$/i }));
    await waitFor(() =>
      expect(putBody).toMatchObject({ model_name: "claude-terrain" }),
    );
  });

  it("surfaces and sends the terrain transcript token limit", async () => {
    mockSystem();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      http.get("*/api/config", () =>
        HttpResponse.json({
          judge: { configured: false, base_url: null, model_name: null, key_hint: null },
          terrain: { ...TERRAIN_DEFAULT, transcript_max_tokens: 200000 },
          default_budget_seconds: 3600,
        }),
      ),
      http.put("*/api/config/terrain-model", async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          judge: { configured: false, base_url: null, model_name: null, key_hint: null },
          terrain: { ...TERRAIN_DEFAULT, transcript_max_tokens: 50000 },
          default_budget_seconds: 3600,
        });
      }),
      http.post("*/api/config/terrain-model/test", () =>
        HttpResponse.json({ ok: true, status: "ok", message: null, model_name: "judge-m" }),
      ),
    );
    renderWithProviders(<SettingsView />);
    // scope to the terrain card (the judge card also has a "transcript token limit" field)
    const terrainCard = (await screen.findByLabelText(/terrain model name/i)).closest("section")!;
    const tokenInput = within(terrainCard).getByLabelText(/transcript token limit/i);
    fireEvent.change(tokenInput, { target: { value: "50000" } });
    fireEvent.click(within(terrainCard).getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(putBody).toMatchObject({ transcript_max_tokens: 50000 }));
  });

  it("'use judge config' clears the override with empty strings", async () => {
    mockSystem();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      http.get("*/api/config", () => HttpResponse.json(UNCONFIGURED)),
      http.put("*/api/config/terrain-model", async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          judge: { configured: false, base_url: null, model_name: null, key_hint: null },
          terrain: TERRAIN_DEFAULT,
          default_budget_seconds: 3600,
        });
      }),
      // Clearing still leaves terrain configured via the judge fallback, so the
      // save auto-verifies the fallback connection.
      http.post("*/api/config/terrain-model/test", () =>
        HttpResponse.json({ ok: true, status: "ok", message: null, model_name: "judge-m" }),
      ),
    );
    renderWithProviders(<SettingsView />);
    fireEvent.click(await screen.findByRole("button", { name: /use judge config/i }));
    await waitFor(() =>
      expect(putBody).toEqual({ key: "", base_url: "", model_name: "" }),
    );
  });

  it("live-tests the terrain model and shows Connected", async () => {
    mockSystem();
    server.use(
      http.get("*/api/config", () => HttpResponse.json(UNCONFIGURED)),
      http.post("*/api/config/terrain-model/test", () =>
        HttpResponse.json({ ok: true, status: "ok", message: null, model_name: "judge-m" }),
      ),
    );
    renderWithProviders(<SettingsView />);
    // The Test button lives in the terrain card's override section (shown directly now).
    const terrainModelName = await screen.findByLabelText(/terrain model name/i);
    const card = terrainModelName.closest("section")!;
    fireEvent.click(within(card).getByRole("button", { name: /^test$/i }));
    // Same success vocabulary as the judge and catalog cards — scoped to the
    // terrain card so the catalog's "Connected" can't satisfy the assertion.
    await waitFor(() =>
      expect(within(card).getByRole("status")).toHaveTextContent("Connected"),
    );
  });

  it("auto-runs the terrain live test after saving an override", async () => {
    mockSystem();
    let tested = false;
    server.use(
      http.get("*/api/config", () => HttpResponse.json(UNCONFIGURED)),
      http.put("*/api/config/terrain-model", () =>
        HttpResponse.json({
          judge: { configured: false, base_url: null, model_name: null, key_hint: null },
          terrain: {
            configured: true,
            uses_judge_default: false,
            base_url: null,
            model_name: "claude-terrain",
            key_hint: null,
          },
          default_budget_seconds: 3600,
        }),
      ),
      http.post("*/api/config/terrain-model/test", () => {
        tested = true;
        return HttpResponse.json({
          ok: true,
          status: "ok",
          message: null,
          model_name: "claude-terrain",
        });
      }),
    );
    renderWithProviders(<SettingsView />);
    const terrainModelName = await screen.findByLabelText(/terrain model name/i);
    fireEvent.change(terrainModelName, { target: { value: "claude-terrain" } });
    const card = terrainModelName.closest("section")!;
    fireEvent.click(within(card).getByRole("button", { name: /^save$/i }));
    // The verification fires automatically — no Test click.
    await waitFor(() =>
      expect(within(card).getByRole("status")).toHaveTextContent("Connected"),
    );
    expect(tested).toBe(true);
  });

  it("shows Verifying connection… while the auto-test is in flight", async () => {
    mockSystem();
    server.use(
      http.get("*/api/config", () => HttpResponse.json(UNCONFIGURED)),
      http.put("*/api/config/model", () =>
        HttpResponse.json({
          judge: {
            configured: true,
            base_url: null,
            model_name: "gpt-4o-mini",
            key_hint: "…1234",
          },
          terrain: TERRAIN_DEFAULT,
          default_budget_seconds: 3600,
        }),
      ),
      http.post("*/api/config/model/test", async () => {
        // Hold the test open long enough to observe the intermediate phase.
        await delay(150);
        return HttpResponse.json({
          ok: true,
          status: "ok",
          message: null,
          model_name: "gpt-4o-mini",
        });
      }),
    );
    renderWithProviders(<SettingsView />);
    const card = (await screen.findByText("Judge model")).closest("section")!;
    fireEvent.change(await within(card).findByLabelText(/api key/i), {
      target: { value: "sk-secret-1234" },
    });
    fireEvent.click(within(card).getByRole("button", { name: /save/i }));
    // Between the PUT resolving and the POST resolving the single status line
    // reads Verifying connection…, then lands on Connected.
    expect(await screen.findByText(/verifying connection/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(within(card).getByRole("status")).toHaveTextContent("Connected"),
    );
  });

  it("disables Save until a field changes and then flags unsaved changes", async () => {
    mockSystem();
    server.use(http.get("*/api/config", () => HttpResponse.json(CONFIGURED)));
    renderWithProviders(<SettingsView />);
    // With nothing edited, the judge Save is disabled and there's no unsaved-changes note.
    const judge = (await screen.findByText("Judge model")).closest("section")!;
    const save = await within(judge).findByRole("button", { name: /^save$/i });
    expect(save).toBeDisabled();
    expect(within(judge).queryByText(/unsaved changes/i)).not.toBeInTheDocument();
    // Editing any field enables Save and surfaces the unsaved-changes state.
    fireEvent.change(within(judge).getByLabelText(/model name/i), {
      target: { value: "gpt-4o" },
    });
    expect(save).toBeEnabled();
    expect(within(judge).getByText(/unsaved changes/i)).toBeInTheDocument();
  });

  it("explains that Test uses the saved key, not unsaved edits (§6)", async () => {
    mockSystem();
    server.use(http.get("*/api/config", () => HttpResponse.json(CONFIGURED)));
    renderWithProviders(<SettingsView />);
    expect(await screen.findByText(/test calls the saved key/i)).toBeInTheDocument();
  });

  it("names the terrain source as the judge model configuration (§6)", async () => {
    mockSystem();
    server.use(http.get("*/api/config", () => HttpResponse.json(UNCONFIGURED)));
    renderWithProviders(<SettingsView />);
    const terrainHeading = await screen.findByText(/terrain attribution model/i);
    const card = terrainHeading.closest("section")!;
    expect(within(card).getByText(/current model/i)).toBeInTheDocument();
    expect(within(card).getByText(/judge model configuration/i)).toBeInTheDocument();
  });

  it("Interface region: the agent-inactivity threshold slider edits the persisted preference", async () => {
    mockSystem();
    server.use(http.get("*/api/config", () => HttpResponse.json(UNCONFIGURED)));
    renderWithProviders(<SettingsView />);

    const region = await screen.findByRole("region", { name: /interface/i });
    expect(within(region).getByText("Live view")).toBeInTheDocument();
    // Default threshold: 3 minutes.
    expect(within(region).getByText("3m 0s")).toBeInTheDocument();

    const slider = within(region).getByRole("slider", {
      name: /agent inactivity warning threshold/i,
    });
    fireEvent.change(slider, { target: { value: "300" } });
    expect(useUiStore.getState().stallThresholdSeconds).toBe(300);
    expect(within(region).getByText("5m 0s")).toBeInTheDocument();
    useUiStore.setState({ stallThresholdSeconds: 180 });
  });
});
