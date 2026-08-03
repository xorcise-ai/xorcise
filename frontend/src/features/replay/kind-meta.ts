import {
  Terminal,
  FileEdit,
  FileText,
  MousePointerClick,
  Eye,
  Wrench,
  Boxes,
  Search,
  Flag,
  Sparkles,
  MessageSquare,
  AlertTriangle,
  Info,
  Gauge,
  HelpCircle,
  type LucideIcon,
} from "lucide-react";
import type { AgentEvent, AgentEventKind } from "@/lib/api/types";

export type KindGroup = "conversation" | "action" | "debug";

export interface KindMeta {
  icon: LucideIcon;
  /** Text/icon color utility (one of the semantic --color-* tokens). */
  colorClass: string;
  /** Left-border accent utility to match `colorClass`. */
  accentClass: string;
  /** Background/dot fill utility matching `colorClass` — a LITERAL `bg-*` string
   * (Tailwind's JIT only emits CSS for class names it can scan literally, so this
   * cannot be derived from `colorClass` at runtime). Used by the Timeline/Terrain dots. */
  dotClass: string;
  /** Short uppercase label shown in the card chrome. */
  label: string;
  group: KindGroup;
}

/** Kind → render metadata for all 18 AgentEventKind values (closed enum). `group`
 * drives fold/Debug-toggle behavior in `ReplayTimeline`: "debug" (metric, unknown) is
 * hidden unless Debug is on; "conversation" (message, thinking) is the narrative thread;
 * everything else is an "action".
 *
 * COLORS are assigned ONLY for kinds the real harness adapters (Claude Code, OpenHands, the
 * shared gen-ai extractor) actually emit, with high contrast between them: assistant `message`
 * = blue, `thinking` = violet, `terminal_*` (exec shell) = green, `file_*` = orange, `tool_*`
 * = rose. The USER prompt is gold — but user vs assistant are the same `message` kind, so that
 * split lives in `eventColor()` (role-aware), not here. Kinds NO harness emits (mcp_*, finding,
 * flag, browser_*) fold onto the `toolcall` (rose) family rather than reserving a distinct
 * color. `metric`/`unknown` are the muted "debug" group. */
export const KIND_META: Record<AgentEventKind, KindMeta> = {
  message: {
    icon: MessageSquare,
    colorClass: "text-assistant",
    accentClass: "border-l-assistant",
    dotClass: "bg-assistant",
    label: "CoT",
    group: "conversation",
  },
  thinking: {
    icon: Sparkles,
    colorClass: "text-think",
    accentClass: "border-l-think",
    dotClass: "bg-think",
    label: "thinking",
    group: "conversation",
  },
  terminal_command: {
    icon: Terminal,
    colorClass: "text-terminal",
    accentClass: "border-l-terminal",
    dotClass: "bg-terminal",
    label: "terminal",
    group: "action",
  },
  terminal_output: {
    icon: Terminal,
    colorClass: "text-terminal",
    accentClass: "border-l-terminal",
    dotClass: "bg-terminal",
    label: "output",
    group: "action",
  },
  file_edit: {
    icon: FileEdit,
    colorClass: "text-file",
    accentClass: "border-l-file",
    dotClass: "bg-file",
    label: "file edit",
    group: "action",
  },
  file_read: {
    icon: FileText,
    colorClass: "text-file",
    accentClass: "border-l-file",
    dotClass: "bg-file",
    label: "file read",
    group: "action",
  },
  browser_action: {
    icon: MousePointerClick,
    colorClass: "text-toolcall",
    accentClass: "border-l-toolcall",
    dotClass: "bg-toolcall",
    label: "browser",
    group: "action",
  },
  browser_observation: {
    icon: Eye,
    colorClass: "text-toolcall",
    accentClass: "border-l-toolcall",
    dotClass: "bg-toolcall",
    label: "browser",
    group: "action",
  },
  tool_call: {
    icon: Wrench,
    colorClass: "text-toolcall",
    accentClass: "border-l-toolcall",
    dotClass: "bg-toolcall",
    label: "tool",
    group: "action",
  },
  tool_result: {
    icon: Wrench,
    colorClass: "text-toolcall",
    accentClass: "border-l-toolcall",
    dotClass: "bg-toolcall",
    label: "tool result",
    group: "action",
  },
  mcp_call: {
    icon: Boxes,
    colorClass: "text-toolcall",
    accentClass: "border-l-toolcall",
    dotClass: "bg-toolcall",
    label: "mcp",
    group: "action",
  },
  mcp_result: {
    icon: Boxes,
    colorClass: "text-toolcall",
    accentClass: "border-l-toolcall",
    dotClass: "bg-toolcall",
    label: "mcp result",
    group: "action",
  },
  finding: {
    icon: Search,
    colorClass: "text-toolcall",
    accentClass: "border-l-toolcall",
    dotClass: "bg-toolcall",
    label: "finding",
    group: "action",
  },
  flag: {
    icon: Flag,
    colorClass: "text-toolcall",
    accentClass: "border-l-toolcall",
    dotClass: "bg-toolcall",
    label: "flag",
    group: "action",
  },
  error: {
    icon: AlertTriangle,
    colorClass: "text-err",
    accentClass: "border-l-err",
    dotClass: "bg-err",
    label: "error",
    group: "action",
  },
  status: {
    icon: Info,
    colorClass: "text-text-secondary",
    accentClass: "border-l-border",
    dotClass: "bg-text-secondary",
    label: "status",
    group: "action",
  },
  metric: {
    icon: Gauge,
    colorClass: "text-text-tertiary",
    accentClass: "border-l-border",
    dotClass: "bg-text-tertiary",
    label: "metric",
    group: "debug",
  },
  unknown: {
    icon: HelpCircle,
    colorClass: "text-text-tertiary",
    accentClass: "border-l-border",
    dotClass: "bg-text-tertiary",
    label: "unknown",
    group: "debug",
  },
};

/** Color classes for an event — role-aware. Everything is colored by `kind` via KIND_META, EXCEPT
 * a `message`, whose color depends on the speaker: the USER prompt is gold (`user`), the assistant
 * is blue (`assistant`). This is the one place role affects color; source_agent never does (guard:
 * event-card.test.tsx). */
export function eventColor(
  kind: AgentEventKind,
  role: string | undefined,
): { colorClass: string; accentClass: string; dotClass: string } {
  if (kind === "message" && role === "user") {
    return { colorClass: "text-user", accentClass: "border-l-user", dotClass: "bg-user" };
  }
  const m = KIND_META[kind] ?? KIND_META.unknown;
  return { colorClass: m.colorClass, accentClass: m.accentClass, dotClass: m.dotClass };
}

/** The DISTINCT colour families the Timeline legend renders — one swatch per colour, not per kind
 * (many kinds share a colour: every tool/mcp/browser/finding/flag kind is `bg-toolcall`, both
 * file kinds are `bg-file`, …). The legend filters these to the families actually present in a
 * trace, keyed by `dotClass`. `user` (gold) is role-derived (see `eventColor`); every other family
 * maps straight to a kind's `KIND_META.dotClass`. Order = reading order in the legend. */
export interface ColorFamily {
  /** Human-readable family name (the kinds it covers). */
  label: string;
  /** The `bg-*` token — matches the corresponding events' resolved `dotClass`. */
  dotClass: string;
}

export const COLOR_FAMILIES: ColorFamily[] = [
  { label: "User Prompt", dotClass: "bg-user" },
  { label: "Agent CoT", dotClass: "bg-assistant" },
  { label: "Agent Thinking", dotClass: "bg-think" },
  { label: "Terminal", dotClass: "bg-terminal" },
  { label: "File", dotClass: "bg-file" },
  { label: "Tool", dotClass: "bg-toolcall" },
  { label: "Error", dotClass: "bg-err" },
  { label: "Status", dotClass: "bg-text-secondary" },
  { label: "Debug", dotClass: "bg-text-tertiary" },
];

/** The event's DISPLAY name: operator-facing "Agent <action>" vocabulary derived from
 * kind+role in the DISPLAY LAYER ONLY — the adapter-baked `event.title` ("assistant
 * message", "terminal", …) is left untouched (renaming it in the adapters would bust the
 * versioned agent_events cache) and stays available as the hover tooltip + View-raw. */
export function displayLabel(
  event: Pick<AgentEvent, "kind" | "role" | "title">,
): { title: string; badge: string } {
  const meta = KIND_META[event.kind] ?? KIND_META.unknown;
  switch (event.kind) {
    case "message":
      return event.role === "user"
        ? { title: "User Prompt", badge: "prompt" }
        : { title: "Agent CoT", badge: meta.label };
    case "thinking":
      return { title: "Agent Thinking", badge: meta.label };
    case "terminal_command":
      return { title: "Agent Terminal", badge: meta.label };
    case "terminal_output":
      return { title: "Terminal Output", badge: meta.label };
    case "file_edit":
      return { title: "Agent File Edit", badge: meta.label };
    case "file_read":
      return { title: "Agent File Read", badge: meta.label };
    case "tool_call":
      // The raw title IS the tool name — prefix it so the row reads as an agent action.
      return { title: event.title ? `Agent ${event.title}` : "Agent Tool", badge: meta.label };
    case "tool_result":
      return { title: "Tool Result", badge: meta.label };
    case "browser_action":
    case "browser_observation":
      return { title: "Agent Browser", badge: meta.label };
    case "mcp_call":
    case "mcp_result":
      return { title: "Agent MCP", badge: meta.label };
    case "finding":
      return { title: "Agent Finding", badge: meta.label };
    case "flag":
      return { title: "Flag Claim", badge: meta.label };
    // error / status / metric / unknown: the raw title is already the best description.
    default:
      return { title: event.title || meta.label, badge: meta.label };
  }
}

/** An event's wall-clock receipt time as a compact HH:MM:SS (local time, 24h). */
export function formatEventTime(ts: string | null | undefined): string {
  const ms = ts ? Date.parse(ts) : NaN;
  if (Number.isNaN(ms)) return "";
  return new Date(ms).toLocaleTimeString(undefined, {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** A span duration as "850ms" / "4.2s" / "1m 5s". */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${Number.isInteger(s) ? s : s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rest = Math.round(s % 60);
  return rest ? `${m}m ${rest}s` : `${m}m`;
}
