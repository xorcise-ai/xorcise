// Display grouping over AgentEventKind (XOR harness capability matrix). The wire profile is
// TOTAL over every kind (see HarnessCapabilityProfile), but a per-kind matrix is too dense to
// read at a glance — these groups are the unit the capability matrix renders one row/dot per.
// `unknown` is deliberately excluded: it is the adapter's escape hatch, not a declared capability,
// so it never earns its own row.
import type { HarnessCapabilityProfile } from "@/lib/api/types";

export type GroupLevel = "supported" | "partial" | "unsupported";

export interface DisplayGroup {
  readonly id: string;
  readonly label: string;
  readonly kinds: readonly string[];
}

export const DISPLAY_GROUPS: readonly DisplayGroup[] = [
  { id: "messages", label: "Agent messages", kinds: ["message"] },
  { id: "thinking", label: "Thinking / CoT", kinds: ["thinking"] },
  {
    id: "terminal",
    label: "Terminal",
    kinds: ["terminal_command", "terminal_output"],
  },
  { id: "files", label: "File edits", kinds: ["file_edit", "file_read"] },
  {
    id: "browser",
    label: "Browser",
    kinds: ["browser_action", "browser_observation"],
  },
  { id: "tools", label: "Tool calls", kinds: ["tool_call", "tool_result"] },
  { id: "mcp", label: "MCP", kinds: ["mcp_call", "mcp_result"] },
  { id: "findings", label: "Findings & flags", kinds: ["finding", "flag"] },
  { id: "errors", label: "Model refusals", kinds: ["error"] },
  {
    id: "health",
    label: "Status / metrics",
    kinds: ["status", "metric"],
  },
] as const;

/** Best member kind wins: supported > partial > unsupported. A group with no declared
 * member kind (missing from `profile.kinds`) is treated as unsupported for that kind. */
export function groupLevel(
  profile: HarnessCapabilityProfile,
  group: DisplayGroup,
): GroupLevel {
  const levels = group.kinds.map((k) => profile.kinds[k] ?? "unsupported");
  if (levels.includes("supported")) return "supported";
  if (levels.includes("partial")) return "partial";
  return "unsupported";
}

/** First non-empty note among the group's member kinds — the honest user-facing gap
 * sentence, rendered verbatim so the wording never forks from the adapter's own note. */
export function groupNote(
  profile: HarnessCapabilityProfile,
  group: DisplayGroup,
): string | undefined {
  return group.kinds.map((k) => profile.notes?.[k]).find(Boolean);
}
