import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor, render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { agentFixture } from "@/test/fixtures";
import { useToastStore } from "@/stores/toasts";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import { RegisterAgentPage } from "./register-agent-page";

beforeEach(() => {
  push.mockClear();
  useToastStore.setState({ toasts: [] });
});

describe("RegisterAgentPage — register mode", () => {
  it("renders the numbered step cards in order", async () => {
    renderWithProviders(<RegisterAgentPage editName={null} />);
    // The step number lives ONLY in the amber chip beside each heading — the heading text
    // itself is the title alone (no duplicated numeral, e.g. NOT "1 · Harness").
    const headings = screen.getAllByRole("heading", { level: 2 });
    const titles = headings.map((h) => h.textContent);
    expect(titles).toEqual([
      "Name the agent",
      "Choose a harness",
      "Model config",
      "Launch config",
      "Review & register",
    ]);
    for (const title of titles) {
      expect(title).not.toMatch(/^\d+\s*·/);
    }
  });

  it("requires a name: submit with blank name shows inline error, no POST", async () => {
    let posted = false;
    server.use(
      http.post("*/api/agents", () => {
        posted = true;
        return HttpResponse.json({ id: "x", name: "" });
      }),
    );
    renderWithProviders(<RegisterAgentPage editName={null} />);

    const submit = screen.getByRole("button", { name: /^register agent$/i });
    expect(submit).toBeDisabled();
    expect(submit).toHaveAccessibleDescription("Name the agent and choose a harness to register.");
    fireEvent.blur(screen.getByLabelText(/name/i));
    expect(screen.getByText(/name is required/i)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/name is required/i);

    // Clicking the (disabled) button never fires a submit while the form is invalid.
    fireEvent.click(submit);
    expect(posted).toBe(false);
  });

  // #141: every agent has a kind, so registering needs a deliberate harness choice.
  it("keeps Register agent disabled, saying why, until a harness is chosen", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post("*/api/agents", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...agentFixture({ name: "scout" }), warnings: [] });
      }),
    );
    renderWithProviders(<RegisterAgentPage editName={null} />);
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "scout" } });

    const submit = screen.getByRole("button", { name: /^register agent$/i });
    expect(submit).toBeDisabled();
    expect(submit).toHaveAccessibleDescription("Choose a harness to register.");
    expect(screen.getByText("Harness").nextElementSibling).toHaveTextContent("Not selected");
    for (const radio of screen.getAllByRole("radio", { name: /openhands|claude|codex|generic/i })) {
      expect(radio).toHaveAttribute("aria-checked", "false");
    }

    fireEvent.click(screen.getByRole("radio", { name: /^generic/i }));
    expect(submit).toBeEnabled();
    expect(submit).not.toHaveAccessibleDescription();
    expect(screen.getByText("Harness").nextElementSibling).toHaveTextContent(/^Generic$/);
    fireEvent.click(submit);
    await waitFor(() => expect(body).toMatchObject({ name: "scout", kind: "generic" }));
  });

  it("sends a blank kind when the Generic ID is cleared, which the server saves as generic", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post("*/api/agents", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...agentFixture({ name: "scout" }), warnings: [] });
      }),
    );
    renderWithProviders(<RegisterAgentPage editName={null} />);
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "scout" } });
    fireEvent.click(screen.getByRole("radio", { name: /^generic/i }));
    fireEvent.change(screen.getByLabelText(/harness id/i), { target: { value: "" } });

    expect(screen.getByText("Harness").nextElementSibling).toHaveTextContent(/^Generic$/);
    fireEvent.click(screen.getByRole("button", { name: /^register agent$/i }));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ name: "scout", kind: null });
  });

  it("shows the server's warning about the saved kind once the agent is registered", async () => {
    // The page's own check needs GET /harnesses; when that fails it cannot judge. The server's
    // answer to the POST still can, so it is shown either way.
    server.use(
      http.get("*/api/harnesses", () => HttpResponse.json({}, { status: 500 })),
      http.post("*/api/agents", () =>
        HttpResponse.json({
          ...agentFixture({ name: "typo", kind: "openhand" }),
          warnings: ["'openhand' is not a registered harness; did you mean 'openhands'?"],
        }),
      ),
    );
    renderWithProviders(<RegisterAgentPage editName={null} />);
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "typo" } });
    fireEvent.click(screen.getByRole("radio", { name: /^generic/i }));
    fireEvent.change(screen.getByLabelText(/harness id/i), { target: { value: "openhand" } });
    fireEvent.click(screen.getByRole("button", { name: /^register agent$/i }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/agents"));
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        tone: "warn",
        title: "Registered “typo” with a warning",
        body: "'openhand' is not a registered harness; did you mean 'openhands'?",
      }),
    ]);
  });

  it("raises no toast when the server has nothing to warn about", async () => {
    server.use(
      http.post("*/api/agents", () =>
        HttpResponse.json({ ...agentFixture({ name: "scout", kind: "codex" }), warnings: [] }),
      ),
    );
    renderWithProviders(<RegisterAgentPage editName={null} />);
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "scout" } });
    fireEvent.click(screen.getByRole("radio", { name: /codex/i }));
    fireEvent.click(screen.getByRole("button", { name: /^register agent$/i }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/agents"));
    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it("submits the declaration and navigates to /agents on success", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post("*/api/agents", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: "x", name: "scout" });
      }),
    );
    renderWithProviders(<RegisterAgentPage editName={null} />);

    fireEvent.change(screen.getByLabelText(/name/i), {
      target: { value: "scout" },
    });
    fireEvent.click(screen.getByRole("radio", { name: /codex/i }));
    fireEvent.click(screen.getByRole("button", { name: /^register agent$/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({
      name: "scout",
      kind: "codex",
      endpoint: null,
      otel: null,
      model: null,
    });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/agents"));
  });

  it("surfaces 409 as 'That name is already taken.'", async () => {
    server.use(
      http.post("*/api/agents", () =>
        HttpResponse.json(
          { detail: "agent 'scout' already registered" },
          { status: 409 },
        ),
      ),
    );
    renderWithProviders(<RegisterAgentPage editName={null} />);

    fireEvent.change(screen.getByLabelText(/name/i), {
      target: { value: "scout" },
    });
    fireEvent.click(screen.getByRole("radio", { name: /openhands/i }));
    fireEvent.click(screen.getByRole("button", { name: /^register agent$/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        /that name is already taken/i,
      ),
    );
    expect(push).not.toHaveBeenCalled();
  });

  it("shows only the selected harness's visibility and launch conditions", async () => {
    renderWithProviders(<RegisterAgentPage editName={null} />);
    fireEvent.click(screen.getByRole("radio", { name: /codex/i }));

    await waitFor(() =>
      expect(screen.getByText(/export span support/i)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getAllByText(/codex exec --sandbox/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/preview only/i)).toBeInTheDocument();
    expect(screen.getAllByText(/command generated per run/i)).not.toHaveLength(0);
    expect(screen.queryByText("Host")).not.toBeInTheDocument();
    expect(screen.queryByText(/headless · command generated/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/view full command/i)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /harness tips/i })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /additional mission preamble/i }),
    ).toBeInTheDocument();
    const previewToggles = screen.getAllByRole("button", { name: /show more/i });
    expect(previewToggles).toHaveLength(2);
    expect(previewToggles[0]).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(previewToggles[0]);
    expect(screen.getByRole("button", { name: /show less/i })).toHaveAttribute(
      "aria-expanded",
      "true",
    );

    expect(screen.getByLabelText(/user messages: supported/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/agent messages: not supported/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/model refusals: not supported/i)).toBeInTheDocument();
    expect(
      screen.getByText("Model refusal details are not exported."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/review visibility limitations/i)).not.toBeInTheDocument();
  });

  it("advertises refusal support for Claude Code and OpenHands", async () => {
    renderWithProviders(<RegisterAgentPage editName={null} />);

    fireEvent.click(screen.getByRole("radio", { name: /claude code/i }));
    await waitFor(() =>
      expect(screen.getByLabelText(/model refusals: supported/i)).toBeInTheDocument(),
    );
    expect(
      screen.queryByText("Model refusal details are not exported."),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: /openhands/i }));
    await waitFor(() =>
      expect(screen.getByLabelText(/model refusals: supported/i)).toBeInTheDocument(),
    );
  });

  // The generic adapter maps what it can, at best: which spans an arbitrary agent exports is
  // unknown, so Generic reads as unknown, never as a count of supported spans.
  it("keeps the Generic harness's export capabilities unknown, not supported", async () => {
    renderWithProviders(<RegisterAgentPage editName={null} />);
    fireEvent.click(screen.getByRole("radio", { name: /^generic/i }));

    await waitFor(() => expect(screen.getByText("9 unknown")).toBeInTheDocument());
    expect(
      screen.getByText("Generic adapter · best effort"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/\d+ supported/)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/thinking \/ cot: unknown/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/thinking \/ cot: supported/i)).not.toBeInTheDocument();
    expect(screen.getByText("Visibility").nextElementSibling).toHaveTextContent(
      /^Unverified · capabilities unknown$/,
    );
  });

  it("keeps a blank Harness ID unknown too, since it saves as generic", async () => {
    renderWithProviders(<RegisterAgentPage editName={null} />);
    fireEvent.click(screen.getByRole("radio", { name: /^generic/i }));
    fireEvent.change(screen.getByLabelText(/harness id/i), { target: { value: "" } });
    await waitFor(() => expect(screen.getByText("9 unknown")).toBeInTheDocument());
  });

  it("profiles a padded built-in slug as that harness, since it saves as one", async () => {
    renderWithProviders(<RegisterAgentPage editName={null} />);
    fireEvent.click(screen.getByRole("radio", { name: /^generic/i }));
    fireEvent.change(screen.getByLabelText(/harness id/i), { target: { value: " codex " } });
    await waitFor(() =>
      expect(screen.getByLabelText(/agent messages: not supported/i)).toBeInTheDocument(),
    );
    expect(screen.getByText("Harness").nextElementSibling).toHaveTextContent(/^Codex CLI$/);
  });

  it("keeps your own label's export capabilities unknown, not supported", async () => {
    renderWithProviders(<RegisterAgentPage editName={null} />);
    fireEvent.click(screen.getByRole("radio", { name: /^generic/i }));
    fireEvent.change(screen.getByLabelText(/harness id/i), { target: { value: "my-agent-cli" } });

    await waitFor(() =>
      expect(screen.getByText("9 unknown")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText(/user messages: unknown/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/agent messages: unknown/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/user messages: supported/i)).not.toBeInTheDocument();
    expect(screen.getByText("Harness").nextElementSibling).toHaveTextContent(/^my-agent-cli$/);
  });

  it("offers harness-specific free-text model suggestions in the optional harness config", async () => {
    renderWithProviders(<RegisterAgentPage editName={null} />);
    fireEvent.click(screen.getByRole("radio", { name: /claude code/i }));

    await waitFor(() => expect(screen.getByText("claude-opus-4-8")).toBeInTheDocument());
    fireEvent.click(screen.getByText("claude-opus-4-8"));
    expect(screen.getByRole("combobox", { name: /^model/i })).toHaveValue(
      "claude-opus-4-8",
    );

    // Suggestions are shortcuts, not validation — arbitrary disclosed IDs remain accepted.
    fireEvent.change(screen.getByRole("combobox", { name: /^model/i }), {
      target: { value: "my-provider/custom-model" },
    });
    expect(screen.getByRole("combobox", { name: /^model/i })).toHaveValue(
      "my-provider/custom-model",
    );
  });

  it("adds the selected model to the Codex launch preview", async () => {
    renderWithProviders(<RegisterAgentPage editName={null} />);

    fireEvent.click(screen.getByRole("radio", { name: /codex/i }));
    await screen.findByText(/codex exec/i);
    fireEvent.change(screen.getByRole("combobox", { name: /^model/i }), {
      target: { value: "gpt-5.6-terra" },
    });
    await waitFor(() =>
      expect(screen.getByText(/codex exec/i)).toHaveTextContent(
        /--model gpt-5\.6-terra \{mission\}/,
      ),
    );

  });

  it("adds the selected model to the Claude launch preview", async () => {
    renderWithProviders(<RegisterAgentPage editName={null} />);

    fireEvent.click(screen.getByRole("radio", { name: /claude code/i }));
    await screen.findByText(/claude --permission-mode/i);
    fireEvent.change(screen.getByRole("combobox", { name: /^model/i }), {
      target: { value: "claude-sonnet-5" },
    });
    await waitFor(() =>
      expect(screen.getByText(/claude --permission-mode/i)).toHaveTextContent(
        /--model claude-sonnet-5 -p \{mission\}/,
      ),
    );
  });

  it("renders a customized multi-line command template verbatim", async () => {
    renderWithProviders(<RegisterAgentPage editName={null} />);

    fireEvent.click(screen.getByRole("radio", { name: /claude code/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^customize$/i }));
    fireEvent.change(screen.getByLabelText(/launch command template/i), {
      target: { value: "run-wrapper \\\n  claude -p {mission}" },
    });
    fireEvent.click(screen.getByRole("button", { name: /apply overrides/i }));

    // The preview wraps and preserves the operator's line structure — no whitespace collapse.
    const preview = await screen.findByText(/run-wrapper/);
    expect(preview.textContent).toBe("run-wrapper \\\n  claude -p {mission}");
  });

  it("registers agent-specific launch settings instead of provider defaults", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post("*/api/agents", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(agentFixture({ name: "scout", kind: "codex" }));
      }),
    );
    renderWithProviders(<RegisterAgentPage editName={null} />);

    fireEvent.click(screen.getByRole("radio", { name: /codex/i }));
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "scout" } });
    fireEvent.click(await screen.findByRole("button", { name: /^customize$/i }));
    fireEvent.change(screen.getByLabelText(/launch command template/i), {
      target: { value: "team-codex --prompt {mission}" },
    });
    fireEvent.change(screen.getByLabelText(/agent-specific tips/i), {
      target: { value: "Use the team wrapper.\nConfirm authentication." },
    });
    fireEvent.change(screen.getByLabelText(/extra mission preamble/i), {
      target: { value: "Follow the team procedure." },
    });
    fireEvent.click(screen.getByRole("button", { name: /apply overrides/i }));
    fireEvent.click(screen.getByRole("button", { name: /^register agent$/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({
      launch_command_template: "team-codex --prompt {mission}",
      launch_tips: ["Use the team wrapper.", "Confirm authentication."],
      mission_preamble: ["Follow the team procedure."],
    });
  });

  it("saves execution context that controls generated run-control and telemetry addresses", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post("*/api/agents", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(agentFixture({ name: "boxed", kind: "codex" }));
      }),
    );
    renderWithProviders(<RegisterAgentPage editName={null} />);

    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "boxed" } });
    fireEvent.click(screen.getByRole("radio", { name: /codex/i }));
    expect(
      screen.queryByRole("radio", { name: /harness default \(host\)/i }),
    ).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /^customize$/i }));
    await waitFor(() =>
      expect(
        screen.getByRole("radio", { name: /harness default \(host\)/i }),
      ).toBeChecked(),
    );
    expect(screen.getByText(/uses loopback addresses/i)).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("radio", { name: /^container$/i }),
    );
    expect(screen.getByText(/uses container-reachable addresses/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /apply overrides/i }));
    expect(screen.getByText(/container · reachable host/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^register agent$/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ launch_mode: "container" });
  });

  it("does not ask for unused agent-file metadata", () => {
    renderWithProviders(<RegisterAgentPage editName={null} />);
    expect(screen.queryByText(/agent file/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/choose a file/i)).not.toBeInTheDocument();
  });
});

describe("RegisterAgentPage — edit mode", () => {
  it("seeds fields from the loaded agent and PUTs on the CURRENT name", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([
          agentFixture({
            name: "scout",
            kind: "openhands",
            model: "claude-opus-4-8",
            endpoint: "/legacy/agent.py",
          }),
        ]),
      ),
    );
    let body: Record<string, unknown> | null = null;
    server.use(
      http.put("*/api/agents/scout", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(agentFixture({ name: "scout", model: "claude-haiku" }));
      }),
    );
    renderWithProviders(<RegisterAgentPage editName="scout" />);

    await waitFor(() =>
      expect(screen.getByDisplayValue("scout")).toBeInTheDocument(),
    );
    expect(screen.getByDisplayValue("claude-opus-4-8")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: /^model/i }), {
      target: { value: "claude-haiku" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({
      name: "scout",
      model: "claude-haiku",
      endpoint: "/legacy/agent.py",
    });
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/agents/detail?name=scout"),
    );
  });

  it("rename navigates to the NEW name's detail", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout", kind: "openhands" })]),
      ),
    );
    let body: Record<string, unknown> | null = null;
    server.use(
      // The handler path IS the assertion the PUT routes on the OLD (current) name.
      http.put("*/api/agents/scout", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(agentFixture({ name: "ranger" }));
      }),
    );
    renderWithProviders(<RegisterAgentPage editName="scout" />);

    await waitFor(() =>
      expect(screen.getByDisplayValue("scout")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByDisplayValue("scout"), {
      target: { value: "ranger" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ name: "ranger" });
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/agents/detail?name=ranger"),
    );
  });

  it("shows the renaming-keeps-history note", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
    );
    renderWithProviders(<RegisterAgentPage editName="scout" />);
    await waitFor(() =>
      expect(screen.getByDisplayValue("scout")).toBeInTheDocument(),
    );
    const note = screen.getByText(/renaming keeps the agent.s history/i);
    expect(note).toBeVisible();

    // The note holds the line reserved for the Name error (so edit mode has no empty gap under
    // the field); a cleared name swaps the error in without moving anything below it.
    const name = screen.getByDisplayValue("scout");
    fireEvent.change(name, { target: { value: "" } });
    fireEvent.blur(name);
    const error = screen.getByRole("alert");
    expect(error).toHaveTextContent("Name is required.");
    expect(error.parentElement?.parentElement).toBe(note.parentElement);
    expect(note).toHaveClass("invisible");
    expect(note).toHaveAttribute("aria-hidden", "true");
  });

  it("unknown editName falls back to register mode with a notice", async () => {
    server.use(http.get("*/api/agents", () => HttpResponse.json([])));
    renderWithProviders(<RegisterAgentPage editName="ghost" />);

    await waitFor(() =>
      expect(
        screen.getByText(/no agent named .ghost./i),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/registering a new agent instead/i),
    ).toBeInTheDocument();
    // Falls back to a blank register form, not a half-seeded edit form.
    expect(screen.getByLabelText(/name/i)).toHaveValue("");
    expect(screen.getByRole("button", { name: /^register agent$/i })).toBeInTheDocument();
  });

  it("the unknown-agent notice can be dismissed", async () => {
    server.use(http.get("*/api/agents", () => HttpResponse.json([])));
    renderWithProviders(<RegisterAgentPage editName="ghost" />);

    await waitFor(() =>
      expect(screen.getByText(/no agent named .ghost./i)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /dismiss notice/i }));
    expect(screen.queryByText(/no agent named/i)).not.toBeInTheDocument();
  });

  // C1 regression guard: your own (non-built-in) saved kind must show up as the Generic card
  // selected AND its kind input populated on the very first paint — not blank.
  it("edit mode with your own kind selects the Generic card and shows it in the kind input", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout", kind: "my-cli" })]),
      ),
    );
    renderWithProviders(<RegisterAgentPage editName="scout" />);

    await waitFor(() =>
      expect(screen.getByDisplayValue("scout")).toBeInTheDocument(),
    );
    expect(screen.getByRole("radio", { name: /^generic/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByLabelText(/harness id/i)).toHaveValue("my-cli");
  });

  it("warns after an edit only when it gave the agent a new unregistered kind", async () => {
    // The CLI's rule: a kind the agent already had is not a new choice, so saving the model on
    // `my-cli` does not repeat the warning; changing the kind to another unregistered one does.
    const warning = "'my-cli2' is not a registered harness";
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout", kind: "my-cli" })]),
      ),
      http.put("*/api/agents/scout", async ({ request }) => {
        const decl = (await request.json()) as { kind: string };
        return HttpResponse.json({
          ...agentFixture({ name: "scout", kind: decl.kind }),
          warnings: [warning.replace("my-cli2", decl.kind)],
        });
      }),
    );
    renderWithProviders(<RegisterAgentPage editName="scout" />);
    await waitFor(() => expect(screen.getByDisplayValue("scout")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(push).toHaveBeenCalledTimes(1));
    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it("toasts the server's warning when an edit changes the kind", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout", kind: "my-cli" })]),
      ),
      http.put("*/api/agents/scout", async ({ request }) => {
        const decl = (await request.json()) as { kind: string };
        return HttpResponse.json({
          ...agentFixture({ name: "scout", kind: decl.kind }),
          warnings: [`'${decl.kind}' is not a registered harness`],
        });
      }),
    );
    renderWithProviders(<RegisterAgentPage editName="scout" />);
    await waitFor(() => expect(screen.getByDisplayValue("scout")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/harness id/i), { target: { value: "my-cli2" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/agents/detail?name=scout"));
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        tone: "warn",
        title: "Saved “scout” with a warning",
        body: "'my-cli2' is not a registered harness",
      }),
    ]);
  });

  // I3 regression guard: the mutation's list invalidation is AWAITED before mutateAsync
  // resolves, so a rename can retire the current name out from under this page before
  // router.push lands. The page must never flash "no agent named…" / a blank register form
  // while that navigation is in flight.
  it("does not flash the unknown-agent notice while a rename's navigation is in flight", async () => {
    let getCalls = 0;
    server.use(
      http.get("*/api/agents", () => {
        getCalls += 1;
        // First load: "scout". Every call after (the mutation's invalidated refetch) reflects
        // the server-confirmed rename — "scout" is gone, "ranger" exists — exactly what a real
        // rename hands back before this page's router.push has actually navigated away.
        return HttpResponse.json(
          getCalls === 1
            ? [agentFixture({ name: "scout", kind: "openhands" })]
            : [agentFixture({ name: "ranger", kind: "openhands" })],
        );
      }),
    );
    let body: Record<string, unknown> | null = null;
    server.use(
      http.put("*/api/agents/scout", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(agentFixture({ name: "ranger" }));
      }),
    );
    renderWithProviders(<RegisterAgentPage editName="scout" />);

    await waitFor(() =>
      expect(screen.getByDisplayValue("scout")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByDisplayValue("scout"), {
      target: { value: "ranger" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(body).not.toBeNull());
    await waitFor(() => expect(getCalls).toBeGreaterThan(1));
    // The invalidated refetch (no longer containing "scout") has already landed — the page
    // must still read as mid-edit, not "unknown agent, registering instead".
    expect(screen.queryByText(/no agent named/i)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/agents/detail?name=ranger"),
    );
  });

  // Ported from register-agent.test.tsx (dialog test deleted alongside the dialog in Task 4).
  // #141: an agent saved without a harness is stored as `generic`; editing it must keep that,
  // shown as the Generic card, not re-tag it or call it "Custom (generic)".
  it("editing a generic agent selects the Generic card and saves it unchanged", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout", kind: "generic" })]),
      ),
    );
    let body: Record<string, unknown> | null = null;
    server.use(
      http.put("*/api/agents/scout", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...agentFixture({ name: "scout" }), warnings: [] });
      }),
    );
    renderWithProviders(<RegisterAgentPage editName="scout" />);

    await waitFor(() =>
      expect(screen.getByDisplayValue("scout")).toBeInTheDocument(),
    );
    expect(screen.getByRole("radio", { name: /^generic/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByLabelText(/harness id/i)).toHaveValue("generic");
    expect(screen.getByText("Harness").nextElementSibling).toHaveTextContent(/^Generic$/);
    expect(screen.queryByText(/custom/i)).not.toBeInTheDocument();
    // Save without touching the harness selector.
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ name: "scout", kind: "generic" });
  });

  // Regression guard for the seed-once fix: the agents list is invalidated (and refetched) by
  // any agent mutation elsewhere in the app while this page sits mounted, and the refetch hands
  // back a referentially-new `agent` object even when its data is unchanged. Reseeding on every
  // such change would silently discard whatever the operator is mid-typing.
  it("does not clobber in-progress typing when the agents query refetches", async () => {
    let calls = 0;
    server.use(
      http.get("*/api/agents", () => {
        calls += 1;
        return HttpResponse.json([
          agentFixture({ name: "scout", model: "claude-opus-4-8" }),
        ]);
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <RegisterAgentPage editName="scout" />
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(screen.getByDisplayValue("claude-opus-4-8")).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByRole("combobox", { name: /^model/i }), {
      target: { value: "typing-in-progress" },
    });

    // Force a background refetch of the agents list (e.g. as if some other mutation
    // elsewhere invalidated it) while the operator is still mid-edit.
    await client.invalidateQueries({ queryKey: ["agents"] });
    await waitFor(() => expect(calls).toBeGreaterThan(1));

    expect(screen.getByDisplayValue("typing-in-progress")).toBeInTheDocument();
  });
});
