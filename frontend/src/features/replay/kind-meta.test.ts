import { describe, it, expect } from "vitest";
import { displayLabel, eventColor, formatDuration, formatEventTime } from "./kind-meta";

describe("eventColor", () => {
  it("colors the user prompt gold, distinct from the assistant (blue)", () => {
    expect(eventColor("message", "user").colorClass).toBe("text-user");
    expect(eventColor("message", "user").dotClass).toBe("bg-user");
    expect(eventColor("message", "agent").colorClass).toBe("text-assistant");
    expect(eventColor("message", "agent").dotClass).toBe("bg-assistant");
  });

  it("colors non-message kinds by kind only (role-independent)", () => {
    expect(eventColor("terminal_command", "tool").colorClass).toBe("text-terminal");
    expect(eventColor("file_edit", "user").colorClass).toBe("text-file");
    expect(eventColor("tool_call", "agent").colorClass).toBe("text-toolcall");
    expect(eventColor("thinking", "agent").colorClass).toBe("text-think");
  });

  it("folds kinds no real harness emits onto the tool family (no reserved colour)", () => {
    for (const kind of ["mcp_call", "mcp_result", "flag", "finding", "browser_action"] as const) {
      expect(eventColor(kind, "agent").colorClass).toBe("text-toolcall");
    }
  });
});

describe("displayLabel", () => {
  const ev = (kind: string, role = "agent", title = "") =>
    displayLabel({ kind, role, title } as Parameters<typeof displayLabel>[0]);

  it("maps a user message to User Prompt and an agent message to Agent CoT", () => {
    expect(ev("message", "user").title).toBe("User Prompt");
    expect(ev("message", "agent", "assistant message").title).toBe("Agent CoT");
  });

  it("maps agent actions to the operator vocabulary", () => {
    expect(ev("thinking").title).toBe("Agent Thinking");
    expect(ev("terminal_command", "agent", "terminal").title).toBe("Agent Terminal");
    expect(ev("terminal_output", "agent", "output").title).toBe("Terminal Output");
    expect(ev("file_edit", "agent", "str_replace /etc/passwd").title).toBe("Agent File Edit");
    expect(ev("file_read").title).toBe("Agent File Read");
    expect(ev("tool_result").title).toBe("Tool Result");
    expect(ev("browser_action").title).toBe("Agent Browser");
    expect(ev("browser_observation").title).toBe("Agent Browser");
    expect(ev("mcp_call").title).toBe("Agent MCP");
    expect(ev("mcp_result").title).toBe("Agent MCP");
    expect(ev("finding").title).toBe("Agent Finding");
    expect(ev("flag").title).toBe("Flag Claim");
  });

  it("prefixes the tool name for a tool_call, with a fallback when untitled", () => {
    expect(ev("tool_call", "agent", "WebSearch").title).toBe("Agent WebSearch");
    expect(ev("tool_call", "agent", "").title).toBe("Agent Tool");
  });

  it("leaves error/status/metric/unknown titles untouched (raw title is the description)", () => {
    expect(ev("error", "agent", "boom").title).toBe("boom");
    expect(ev("status", "agent", "permission gate").title).toBe("permission gate");
    expect(ev("metric", "agent", "token usage").title).toBe("token usage");
    expect(ev("unknown", "agent", "mystery span").title).toBe("mystery span");
  });

  it("gives the user prompt its own badge, distinct from the COT badge", () => {
    expect(ev("message", "user").badge).toBe("prompt");
    expect(ev("message", "agent").badge).toBe("CoT");
    expect(ev("terminal_command").badge).toBe("terminal");
  });
});

describe("formatEventTime / formatDuration", () => {
  it("formats a parseable ts as HH:MM:SS and an unparseable one as empty", () => {
    expect(formatEventTime("2026-07-03T10:04:05Z")).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(formatEventTime("not-a-date")).toBe("");
  });

  it("formats durations across magnitudes", () => {
    expect(formatDuration(850)).toBe("850ms");
    expect(formatDuration(4200)).toBe("4.2s");
    expect(formatDuration(5000)).toBe("5s");
    expect(formatDuration(65_000)).toBe("1m 5s");
    expect(formatDuration(120_000)).toBe("2m");
  });
});
