import { http, HttpResponse } from "msw";
import type { HarnessCapabilityProfile, HarnessDescriptor } from "@/lib/api/types";

// Default handlers. Tests narrow/override per-case with `server.use(...)`.
// Paths are matched with a leading wildcard so any origin (the jsdom origin)
// resolves; the server serves everything under /api.

// Every AgentEventKind. A profile is TOTAL over this set (src/xorcise/core/otel/adapters/base.py
// `profile_from`) — any kind not explicitly given below defaults to "unsupported" here too, so
// this fixture mirrors the real shape without hand-writing all 18 keys per adapter.
const ALL_KINDS = [
  "message", "thinking", "terminal_command", "terminal_output", "file_edit", "file_read",
  "browser_action", "browser_observation", "tool_call", "tool_result", "mcp_call",
  "mcp_result", "finding", "flag", "error", "status", "metric", "unknown",
] as const;

function capabilityProfile(
  name: string,
  version: string,
  supported: readonly string[],
  partial: readonly string[] = [],
  notes: Record<string, string> = {},
  verified = true,
): HarnessCapabilityProfile {
  const kinds: Record<string, "supported" | "partial" | "unsupported"> = {};
  for (const k of ALL_KINDS) {
    kinds[k] = supported.includes(k)
      ? "supported"
      : partial.includes(k)
        ? "partial"
        : "unsupported";
  }
  const messageLevel = kinds.message;
  return {
    adapter_name: name,
    adapter_version: version,
    verified,
    kinds,
    message_roles: { user: messageLevel, agent: messageLevel },
    notes,
  };
}

// The four real adapter profiles (src/xorcise/core/harness_adapters/*/otel.py `capabilities`),
// sorted by adapter_name as the server serves them.
const CAPABILITY_PROFILES: HarnessCapabilityProfile[] = [
  capabilityProfile(
    "claude-code",
    "7",
    [
      "message", "terminal_command", "terminal_output", "file_edit", "file_read",
      "tool_call", "tool_result", "status", "metric", "unknown",
    ],
    [],
    {
      thinking: "Claude Code's OTel exporter does not emit thinking traces.",
      mcp_call: "Claude Code reports MCP calls as generic tool calls, not distinct MCP events.",
    },
  ),
  capabilityProfile(
    "codex",
    "1",
    [
      "terminal_command", "terminal_output", "file_edit", "tool_call", "tool_result",
      "mcp_call", "mcp_result", "status", "metric", "unknown",
    ],
    ["message"],
    {
      message:
        "User prompts only — Codex CLI does not export agent-authored chat messages.",
      thinking: "Codex CLI does not export thinking traces.",
    },
  ),
  capabilityProfile(
    "generic",
    "1",
    ["message", "thinking", "terminal_command", "tool_call", "mcp_call", "flag", "error"],
    [],
    {},
    false,
  ),
  capabilityProfile(
    "openhands",
    "1",
    [
      "message", "thinking", "terminal_command", "terminal_output", "file_edit",
      "file_read", "tool_call", "metric", "unknown",
    ],
    [],
    { tool_result: "OpenHands reports tool calls without separate result events." },
  ),
];

CAPABILITY_PROFILES.find((p) => p.adapter_name === "codex")!.message_roles = {
  user: "supported",
  agent: "unsupported",
};

const profile = (kind: string) =>
  CAPABILITY_PROFILES.find((candidate) => candidate.adapter_name === kind)!;

const HARNESS_DESCRIPTORS: HarnessDescriptor[] = [
  {
    kind: "claude-code",
    display_name: "Claude Code CLI",
    description: "Anthropic's non-interactive coding CLI.",
    model_hints: ["claude-opus-4-8", "claude-sonnet-5", "claude-sonnet-4-6"],
    capabilities: profile("claude-code"),
    launch: {
      launch_modes: ["host"],
      command_template: "claude --permission-mode auto -p {mission}",
      model_flag: "--model",
      model_flag_anchor: "-p {mission}",
      tips: ["Generated addresses follow the saved execution context."],
      mission_preamble: ["Work the objective directly."],
    },
  },
  {
    kind: "codex",
    display_name: "Codex CLI",
    description: "OpenAI's non-interactive coding CLI.",
    model_hints: ["gpt-5.3-codex", "gpt-5.6", "gpt-5.6-terra"],
    capabilities: profile("codex"),
    launch: {
      launch_modes: ["host"],
      command_template: "codex exec --sandbox danger-full-access {mission}",
      model_flag: "--model",
      tips: ["Generated addresses follow the saved execution context."],
      mission_preamble: ["Work the objective directly."],
    },
  },
  {
    kind: "generic",
    display_name: "Custom",
    description: "Any CLI agent — replayed and launched through generic fallbacks.",
    model_hints: [],
    capabilities: profile("generic"),
    launch: {
      launch_modes: ["host", "container"],
      command_template: null,
      tips: [],
      mission_preamble: [],
    },
  },
  {
    kind: "openhands",
    display_name: "OpenHands",
    description: "Autonomous development agent with provider-configurable models.",
    model_hints: ["claude-opus-4-8", "claude-sonnet-5", "gpt-5.6"],
    capabilities: profile("openhands"),
    launch: {
      launch_modes: ["host"],
      command_template: "openhands --headless -t {mission}",
      tips: ["Configure the OpenHands model and credentials before launching."],
      mission_preamble: ["Work the objective directly."],
    },
  },
];

export const handlers = [
  http.get("*/api/health", () => HttpResponse.json({ status: "ok" })),
  http.get("*/api/harnesses", () => HttpResponse.json(HARNESS_DESCRIPTORS)),
  http.get("*/api/harnesses/capabilities", () =>
    HttpResponse.json(CAPABILITY_PROFILES),
  ),
  http.get("*/api/agents", () => HttpResponse.json([])),
  http.get("*/api/missions", () => HttpResponse.json([])),
  http.get("*/api/runs", () => HttpResponse.json([])),
  // The server returns empty records for an unknown run (read_since -> []).
  http.get("*/api/runs/:id/traces", ({ params }) =>
    HttpResponse.json({ run_id: params.id, records: [] }),
  ),
  http.get("*/api/runs/:id/prompt", ({ params }) =>
    HttpResponse.json({ run_id: params.id, prompt: "Solve the mission." }),
  ),
  // Normalized, agent-agnostic replay events. Empty page by default;
  // tests that assert rendered events override a specific id with server.use(...).
  http.get("*/api/runs/:id/events", ({ params }) =>
    HttpResponse.json({
      run_id: params.id,
      source_agent: "generic",
      adapter_name: "generic",
      adapter_version: "1",
      fallback: false,
      next_since: -1,
      next_cursor: { trace_seq: -1, log_seq: -1 },
      counts: {},
      warnings: [],
      events: [],
    }),
  ),
  // Raw OTLP span(s) an event was derived from (drill-down). event_id can contain
  // '/', so this is matched with a trailing wildcard, not a single :param segment.
  // Tests that open the drill-down modal override with server.use(...).
  http.get("*/api/runs/:id/events/*", () =>
    HttpResponse.json({ event_id: "unknown", raw_ref: null, spans: [], logs: [] }),
  ),
  // Harness launch profile (OTel env). Empty by default → the telemetry block is omitted;
  // tests that assert it override a specific id with server.use(...). Widened with
  // correlation/notes/fallback (generic run-agnostic default).
  http.get("*/api/runs/:id/launch-profile", ({ params }) =>
    HttpResponse.json({
      run_id: params.id,
      env: {},
      correlation: "prompt-sentinel",
      notes: [],
      fallback: true,
      launch_mode: "host",
      command: null,
      shell_block: "",
    }),
  ),
  http.get("*/api/catalog/status", () => HttpResponse.json({ state: "connected" })),
  http.get("*/api/system", () =>
    HttpResponse.json({
      role: "all",
      planes: [],
      db_schema: "head",
      catalog: { state: "connected", message: null, last_sync: null },
      remotes: [],
      home: "/home/u/.xorcise",
      db_url: "sqlite:////home/u/.xorcise/xorcise.db",
      topology: "local",
    }),
  ),
  http.get("*/api/config", () =>
    HttpResponse.json({
      judge: { configured: false, base_url: null, model_name: null, key_hint: null, timeout_seconds: 120, transcript_max_bytes: 262144 },
      terrain: { configured: true, uses_judge_default: true, base_url: null, model_name: null, key_hint: null },
      default_budget_seconds: 600,
      catalog: { connected: true, url: "https://catalog.example.com" },
      network: { headscale_url: null, advertise_host: null },
    }),
  ),
  http.get("*/api/missions/:id/manifest", ({ params }) =>
    HttpResponse.json({
      schema_version: "2.0",
      metadata: {
        mission_id: params.id,
        name: String(params.id),
        summary: "",
        objective: "",
      },
      environment: { compose_file: "docker-compose.yml", entry_networks: [] },
      artifacts: [],
      rubric: [],
      checks: [],
      intel: [],
      attachments: [],
      terrain: null,
    }),
  ),
  // No recorded result by default (404). Tests that assert a scorecard override
  // a specific id with server.use(...).
  http.get("*/api/runs/:id/result", () =>
    HttpResponse.json({ detail: "not found" }, { status: 404 }),
  ),
  // No submitted artifacts by default. Tests that assert artifact content
  // override a specific id with server.use(...).
  http.get("*/api/runs/:id/artifacts", () => HttpResponse.json([])),
  // Default empty terrain (v2). Tests that assert terrain content override a specific id
  // with server.use(...).
  http.get("*/api/runs/:id/terrain2", ({ params }) =>
    HttpResponse.json({
      run_id: params.id,
      mission_id: "c1",
      summary: null,
      groups: [],
      nodes: [],
      edges: [],
      updates: [],
      attribution: null,
      objective_id: null,
    }),
  ),
];
