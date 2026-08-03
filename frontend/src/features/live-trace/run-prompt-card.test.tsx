import { describe, it, expect } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { RunPromptCard } from "./run-prompt-card";

describe("RunPromptCard", () => {
  it("renders the run's prompt and a copy button", async () => {
    server.use(
      http.get("*/api/runs/r1/prompt", () =>
        HttpResponse.json({
          run_id: "r1",
          prompt: "Continue Xorcise eval run r1.",
        }),
      ),
    );
    renderWithProviders(<RunPromptCard runId="r1" />);
    await waitFor(() =>
      expect(
        screen.getByText("Continue Xorcise eval run r1."),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: /copy prompt/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Action required/i)).toBeInTheDocument();
  });

  it("surfaces the launch-profile telemetry env as a copyable dotenv block", async () => {
    server.use(
      http.get("*/api/runs/r1/prompt", () =>
        HttpResponse.json({ run_id: "r1", prompt: "Solve it." }),
      ),
      http.get("*/api/runs/r1/launch-profile", () =>
        HttpResponse.json({
          run_id: "r1",
          env: {
            OTEL_EXPORTER_OTLP_ENDPOINT: "http://host.docker.internal:4318",
            OTEL_EXPORTER_OTLP_PROTOCOL: "http/protobuf",
            OTEL_TRACES_EXPORTER: "otlp",
          },
        }),
      ),
    );
    renderWithProviders(<RunPromptCard runId="r1" />);
    await waitFor(() =>
      expect(
        screen.getByText(
          /OTEL_EXPORTER_OTLP_ENDPOINT=http:\/\/host\.docker\.internal:4318/,
        ),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: /copy .*env/i }),
    ).toBeInTheDocument();
  });

  it("consolidates into one launch block, host-only, with a collapsible prompt", async () => {
    server.use(
      http.get("*/api/runs/r1/prompt", () =>
        HttpResponse.json({ run_id: "r1", prompt: "MISSION-BODY-XYZ" }),
      ),
      http.get("*/api/runs/r1/launch-profile", () =>
        HttpResponse.json({
          run_id: "r1",
          env: { OTEL_EXPORTER_OTLP_ENDPOINT: "http://localhost:4318" },
          correlation: "resource-attr",
          notes: [],
          fallback: false,
          launch_mode: "host",
          launch_modes: ["host"],
          command: "claude -p 'solve the CTF'",
          shell_block:
            "export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318\nclaude -p 'solve the CTF'",
          tips: [],
        }),
      ),
    );
    renderWithProviders(<RunPromptCard runId="r1" />);
    // ONE block carries both the env exports and the claude command (same <pre>).
    await waitFor(() =>
      expect(screen.getByText(/export OTEL_EXPORTER_OTLP_ENDPOINT/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/claude -p 'solve the CTF'/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /copy launch command/i }),
    ).toBeInTheDocument();
    // Host-only harness → no container launch toggle.
    expect(screen.queryByText(/container/i)).not.toBeInTheDocument();
    // The mission prompt is collapsed by default and revealed on demand.
    expect(screen.queryByText("MISSION-BODY-XYZ")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /view mission prompt/i }));
    expect(screen.getByText("MISSION-BODY-XYZ")).toBeInTheDocument();
  });

  it("pins the launch-command copy ABOVE the payload, and the payload wraps", async () => {
    // The reported defect was spatial, so jsdom could not see it: the copy button rendered
    // AFTER a `max-h-80 overflow-auto` <pre> whose 818-char lines also scrolled sideways, so
    // it sat ~300px below the clip line of the card's own scroller. Pinning the action above
    // the payload (and wrapping the payload) makes the geometry unable to hide it again.
    server.use(
      http.get("*/api/runs/r1/prompt", () =>
        HttpResponse.json({ run_id: "r1", prompt: "MISSION" }),
      ),
      http.get("*/api/runs/r1/launch-profile", () =>
        HttpResponse.json({
          run_id: "r1",
          env: { OTEL_RESOURCE_ATTRIBUTES: "xorcise.run_id=r1" },
          correlation: "resource-attr",
          notes: [],
          fallback: false,
          launch_mode: "host",
          launch_modes: ["host"],
          command: "codex exec --sandbox danger-full-access 'x'",
          shell_block:
            "export OTEL_RESOURCE_ATTRIBUTES=xorcise.run_id=r1\ncodex exec --sandbox danger-full-access 'x'",
          tips: [],
        }),
      ),
    );
    renderWithProviders(<RunPromptCard runId="r1" fill />);
    const button = await screen.findByRole("button", { name: /copy launch command/i });
    const pre = screen.getByText(/codex exec --sandbox/).closest("pre");
    expect(pre).not.toBeNull();
    // DOCUMENT_POSITION_FOLLOWING = the <pre> comes after the button in document order.
    expect(
      button.compareDocumentPosition(pre as Node) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // The payload wraps instead of owning a horizontal (and a vertical) scroll axis of its own,
    // so the card body is the single scroller in this column.
    expect(pre).toHaveClass("whitespace-pre-wrap");
    expect(pre?.className).not.toContain("overflow-auto");
    // A short block is shown whole — the clamp is for real 3.6kB launch payloads only.
    expect(pre?.className).not.toContain("max-h-");
    expect(screen.queryByRole("button", { name: /show full command/i })).toBeNull();
  });

  it("clamps a long launch block behind a disclosure instead of a nested scroller", async () => {
    const longBlock =
      "export OTEL_RESOURCE_ATTRIBUTES=xorcise.run_id=r1\ncodex exec " +
      "--sandbox danger-full-access ".repeat(30) +
      "'solve it'";
    server.use(
      http.get("*/api/runs/r1/prompt", () =>
        HttpResponse.json({ run_id: "r1", prompt: "MISSION" }),
      ),
      http.get("*/api/runs/r1/launch-profile", () =>
        HttpResponse.json({
          run_id: "r1",
          env: {},
          correlation: "resource-attr",
          notes: [],
          fallback: false,
          launch_mode: "host",
          launch_modes: ["host"],
          command: longBlock,
          shell_block: longBlock,
          tips: ["Run this on the host"],
        }),
      ),
    );
    renderWithProviders(<RunPromptCard runId="r1" fill />);
    const toggle = await screen.findByRole("button", { name: /show full command/i });
    const pre = screen.getByText(/danger-full-access/).closest("pre");
    // Clamped with overflow-hidden — the card body stays the column's single scroller, and the
    // tips below the block are not buried under ~1200px of wrapped payload.
    expect(pre?.className).toContain("max-h-56");
    expect(pre?.className).toContain("overflow-hidden");
    // Nothing is hidden without a way back: the whole block is one click away, and the copy
    // button already carries the full text regardless of what is rendered.
    fireEvent.click(toggle);
    expect(
      screen.getByText(/danger-full-access/).closest("pre")?.className,
    ).not.toContain("max-h-56");
    expect(screen.getByText(/Run this on the host/)).toBeInTheDocument();
  });

  it("renders per-harness launch tips", async () => {
    server.use(
      http.get("*/api/runs/r1/prompt", () =>
        HttpResponse.json({ run_id: "r1", prompt: "Solve it." }),
      ),
      http.get("*/api/runs/r1/launch-profile", () =>
        HttpResponse.json({
          run_id: "r1",
          env: { A: "1" },
          correlation: "resource-attr",
          notes: [],
          fallback: false,
          launch_mode: "host",
          command: "claude -p 'x'",
          shell_block: "export A=1\nclaude -p 'x'",
          tips: ["Restart your shell after exporting these vars so the harness picks them up"],
        }),
      ),
    );
    renderWithProviders(<RunPromptCard runId="r1" />);
    expect(
      await screen.findByText(/Restart your shell after exporting these vars/),
    ).toBeInTheDocument();
  });

  it("refetches the prompt for the launch mode when the toggle flips", async () => {
    // The reported bug: flipping the host/container toggle re-fetched only the telemetry env, so
    // the prompt's run-control Base URL stayed frozen at host.docker.internal. The prompt request
    // must carry ?launch_mode= and refetch when the operator flips it.
    server.use(
      http.get("*/api/runs/r1/prompt", ({ request }) => {
        const mode = new URL(request.url).searchParams.get("launch_mode");
        return HttpResponse.json({
          run_id: "r1",
          prompt:
            mode === "container"
              ? "Base URL: http://host.docker.internal:3001"
              : "Base URL: http://127.0.0.1:3001",
        });
      }),
      http.get("*/api/runs/r1/launch-profile", () =>
        HttpResponse.json({
          run_id: "r1",
          env: { OTEL_EXPORTER_OTLP_ENDPOINT: "http://127.0.0.1:4318" },
          correlation: "prompt-sentinel",
          notes: [],
          fallback: true,
          launch_mode: "host",
          launch_modes: ["host", "container"], // dual-mode → the toggle is shown
          command: null, // prompt-only branch (no launch command)
          shell_block: "",
          tips: [],
        }),
      ),
    );
    renderWithProviders(<RunPromptCard runId="r1" />);
    // Default host mode → loopback run-control host.
    await waitFor(() =>
      expect(screen.getByText(/http:\/\/127\.0\.0\.1:3001/)).toBeInTheDocument(),
    );
    // The container URL is absent in host mode — matched on the full URL, not the bare hostname
    // (which the "a container (host.docker.internal)" toggle button always shows).
    expect(
      screen.queryByText(/http:\/\/host\.docker\.internal:3001/),
    ).not.toBeInTheDocument();
    // Flip to container → the prompt refetches and now names host.docker.internal.
    fireEvent.click(screen.getByRole("button", { name: /a container/i }));
    await waitFor(() =>
      expect(screen.getByText(/http:\/\/host\.docker\.internal:3001/)).toBeInTheDocument(),
    );
  });

  it("omits the telemetry block when no collector is configured (empty env)", async () => {
    server.use(
      http.get("*/api/runs/r2/prompt", () =>
        HttpResponse.json({ run_id: "r2", prompt: "Solve it." }),
      ),
      http.get("*/api/runs/r2/launch-profile", () =>
        HttpResponse.json({ run_id: "r2", env: {} }),
      ),
    );
    renderWithProviders(<RunPromptCard runId="r2" />);
    await waitFor(() =>
      expect(screen.getByText("Solve it.")).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Telemetry/i)).not.toBeInTheDocument();
  });
});

describe("RunPromptCard while the environment is still starting", () => {
  it("does not tell the operator to launch before the targets exist", async () => {
    // "Action required — start your agent" alongside "Environment: Starting" invites launching
    // into an environment whose targets have not come up — precisely how an agent ends up joined
    // to the tailnet with nothing to reach.
    renderWithProviders(<RunPromptCard runId="r1" awaitingEnvironment />);
    expect(
      await screen.findByText(/Preparing environment/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Action required/i)).toBeNull();
  });

  it("still hands over the launch command so it can be copied ahead of time", async () => {
    renderWithProviders(<RunPromptCard runId="r1" awaitingEnvironment />);
    expect(
      await screen.findByRole("button", { name: /Copy launch command|Copy prompt/i }),
    ).toBeInTheDocument();
  });
});
