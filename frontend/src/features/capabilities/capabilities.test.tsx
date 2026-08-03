import { describe, expect, it } from "vitest";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { createQueryWrapper } from "@/test/render";
import { CapabilityDot } from "./capability-dot";
import { DISPLAY_GROUPS, groupLevel } from "./capability-groups";
import { useCapabilities } from "./use-capabilities";
import type { HarnessCapabilityProfile } from "@/lib/api/types";

const profile = (kinds: Record<string, string>): HarnessCapabilityProfile =>
  ({
    adapter_name: "codex",
    adapter_version: "1",
    verified: true,
    kinds,
    notes: {},
  }) as HarnessCapabilityProfile;

describe("capability display groups", () => {
  it("cover every AgentEventKind except unknown, exactly once", () => {
    const all = DISPLAY_GROUPS.flatMap((g) => g.kinds);
    expect(new Set(all).size).toBe(all.length);
    expect([...all].sort()).toEqual(
      [
        "browser_action",
        "browser_observation",
        "error",
        "file_edit",
        "file_read",
        "finding",
        "flag",
        "mcp_call",
        "mcp_result",
        "message",
        "metric",
        "status",
        "terminal_command",
        "terminal_output",
        "thinking",
        "tool_call",
        "tool_result",
      ].sort(),
    );
  });

  it("labels match the approved spec copy verbatim and stay in this order", () => {
    expect(DISPLAY_GROUPS.map((g) => g.label)).toEqual([
      "Agent messages",
      "Thinking / CoT",
      "Terminal",
      "File edits",
      "Browser",
      "Tool calls",
      "MCP",
      "Findings & flags",
      "Status / errors / metrics",
    ]);
  });

  it("groupLevel: any supported member wins; partial beats unsupported", () => {
    const p = profile({
      message: "partial",
      tool_call: "supported",
      tool_result: "unsupported",
    });
    expect(
      groupLevel(p, DISPLAY_GROUPS.find((g) => g.id === "messages")!),
    ).toBe("partial");
    expect(groupLevel(p, DISPLAY_GROUPS.find((g) => g.id === "tools")!)).toBe(
      "supported",
    );
  });
});

describe("CapabilityDot", () => {
  it("carries state in accessible text, glyph hidden", () => {
    render(
      <CapabilityDot
        level="unsupported"
        label="Thinking / CoT"
        note="not exported"
      />,
    );
    expect(
      screen.getByText(/Thinking \/ CoT: not supported/),
    ).toBeInTheDocument();
  });

  it("never paints state in brand amber (amber = selection only)", () => {
    const { container } = render(
      <CapabilityDot level="supported" label="Terminal" />,
    );
    const dot = container.querySelector("[data-dot]")!;
    expect(dot.getAttribute("class") ?? "").not.toMatch(/primary|amber/);
    expect(dot.getAttribute("style") ?? "").not.toMatch(/--color-primary/);
  });
});

describe("useCapabilities", () => {
  it("fetches the four adapter profiles and indexes them by name", async () => {
    const { result } = renderHook(() => useCapabilities(), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.profiles).toHaveLength(4));

    expect([...result.current.byName.keys()]).toEqual([
      "claude-code",
      "codex",
      "generic",
      "openhands",
    ]);
    const codex = result.current.byName.get("codex")!;
    expect(codex.kinds.message).toBe("partial");
    expect(codex.notes?.message).toMatch(/Codex CLI does not export/);
    expect(result.current.byName.get("generic")!.verified).toBe(false);
  });
});
