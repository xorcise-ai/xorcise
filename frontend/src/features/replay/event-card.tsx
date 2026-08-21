"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/components/ui/cn";
import type { AgentEvent } from "@/lib/api/types";
import { KIND_META, displayLabel, eventColor, formatDuration, formatEventTime } from "./kind-meta";
import { highlightShell, highlightJson, renderAnsi } from "./syntax";

/** The compact right-aligned "HH:MM:SS · 4.2s" stamp every card/row header carries. */
export function EventTime({ event }: { event: Pick<AgentEvent, "ts" | "duration_ms"> }) {
  const time = formatEventTime(event.ts);
  const dur =
    event.duration_ms != null && event.duration_ms > 0 ? formatDuration(event.duration_ms) : null;
  if (!time && !dur) return null;
  return (
    <span
      data-testid="event-time"
      className="ml-auto shrink-0 font-mono text-caption tabular-nums text-text-tertiary"
    >
      {time}
      {dur ? ` · ${dur}` : ""}
    </span>
  );
}

/** Subtle per-span BYOM-attribution status (iteration 2). `action` = attributed and mapped to a
 *  terrain edge; `analyzed` = the model considered it but placed nothing; `pending` = queued;
 *  `in-progress` = a catch-up batch is running now. Non-attributable spans get no dot. */
export type AttributionSpanStatus = "pending" | "in-progress" | "analyzed" | "action";

const STATUS_DOT: Record<AttributionSpanStatus, { cls: string; title: string }> = {
  action: { cls: "bg-ok", title: "Attributed — mapped to a terrain action" },
  analyzed: { cls: "border border-text-tertiary", title: "Analyzed — no terrain action" },
  // Red: this span is NOT attributed — the model has no verdict for it yet (or one never landed).
  pending: { cls: "bg-err", title: "Not attributed" },
  "in-progress": { cls: "bg-primary motion-safe:animate-pulse", title: "Attributing…" },
};

/** A 6px dot conveying a span's attribution status; renders nothing for a non-attributable span. */
export function StatusDot({ status }: { status?: AttributionSpanStatus }) {
  if (!status) return null;
  const s = STATUS_DOT[status];
  return (
    <span
      data-testid="attr-status"
      data-status={status}
      title={s.title}
      aria-label={s.title}
      className={cn("size-1.5 shrink-0 rounded-full", s.cls)}
    />
  );
}

/** Key/value chips for an event's structured `data` (tool args, mcp payloads, metrics). */
function DataChips({ data }: { data: Record<string, string> }) {
  const entries = Object.entries(data ?? {});
  if (entries.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {entries.map(([key, value]) => (
        <span
          key={key}
          className="max-w-full break-words rounded-lg bg-raised px-1.5 py-0.5 font-mono text-caption text-text-secondary"
        >
          {key}: {value}
        </span>
      ))}
    </div>
  );
}

/**
 * Pretty-print a JSON string value; return the original text otherwise. Presentational ONLY —
 * this never mutates `event.data` (the normalized projection), so OTLP / adapters / normalization
 * are unaffected. Exported for unit testing.
 */
export function prettyValue(value: string): string {
  const trimmed = value.trim();
  if (trimmed.length >= 2 && (trimmed[0] === "{" || trimmed[0] === "[")) {
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2);
    } catch {
      // not valid JSON — fall through and show the raw text as-is
    }
  }
  return value;
}

/**
 * Strip ANSI escape sequences (SGR colours, cursor moves, bracketed-paste like
 * `\x1b[?2004l`) from terminal output for clean display. Presentational only.
 * Exported for testing.
 */
const ANSI_CSI = new RegExp(`${String.fromCharCode(27)}\\[[0-9;?]*[ -/]*[@-~]`, "g");

export function stripAnsi(value: string): string {
  return value.replace(ANSI_CSI, "");
}

/**
 * A hover-tooltip explanation for a permission-gate event. Permission gates carry a
 * `data.decision` — explain what the decision means so `decision=reject` isn't mysterious.
 * Returns null for any event that isn't a permission gate. Exported for testing.
 */
export function permissionGateExplanation(event: AgentEvent): string | null {
  const decision = event.data?.decision;
  if (!decision) return null;
  if (decision === "reject" || decision === "deny") {
    return (
      "Blocked by the permission gate — the agent's tool call was denied and did NOT run. " +
      "In a headless / acceptEdits run only file edits are auto-approved; other tools (shell " +
      "commands especially) need interactive approval that a non-interactive run can't give, so " +
      "they're rejected. Expected behaviour, not an error — it's why these terminals show no output."
    );
  }
  if (decision === "approve" || decision === "allow") {
    return "Approved by the permission gate — the tool call was allowed to run.";
  }
  return `Permission-gate decision: ${decision}.`;
}

/**
 * Structured event data (tool args, mcp payloads, file/browser ops). Short scalar values render
 * as compact chips; long or JSON values are pretty-printed in a contained, scrollable block so
 * nothing bleeds out of the card. Dispatches on VALUE SHAPE only — never on `source_agent`.
 */
function DataView({ data }: { data: Record<string, string> }) {
  const entries = Object.entries(data ?? {});
  if (entries.length === 0) return null;
  return (
    <div className="mt-2 min-w-0 space-y-2">
      {entries.map(([key, value]) => {
        const pretty = prettyValue(value);
        const asBlock = pretty.includes("\n") || pretty.length > 60;
        // Colour it as JSON only when it actually looks like JSON (prettyValue re-stringified an
        // object/array); a long plain-text value stays plain.
        const looksJson = /^\s*[[{]/.test(pretty);
        return (
          <div key={key} className="min-w-0">
            {asBlock ? (
              <>
                <div className="font-mono text-label uppercase text-text-tertiary">
                  {key}
                </div>
                <pre className="mt-1 max-h-[24rem] overflow-auto whitespace-pre-wrap break-words rounded-md bg-deepest p-2 font-mono text-dense text-foreground">
                  {looksJson ? highlightJson(pretty) : pretty}
                </pre>
              </>
            ) : (
              <span className="inline-block max-w-full break-words rounded-lg bg-raised px-1.5 py-0.5 font-mono text-caption text-text-secondary">
                {key}: {value}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * The body layout for one event, driven ONLY by `event.kind` (the closed
 * 18-value enum) — never by `event.source_agent` or any span-name inference.
 */
function EventBody({ event }: { event: AgentEvent }) {
  switch (event.kind) {
    case "terminal_command":
    case "terminal_output":
      return <TerminalPre body={event.body} kind={event.kind} />;

    case "message":
    case "thinking":
      return event.body ? (
        <p className="mt-2 whitespace-pre-wrap break-words text-body text-foreground">
          {event.body}
        </p>
      ) : null;

    case "file_edit":
    case "file_read":
    case "browser_action":
    case "browser_observation":
    case "tool_call":
    case "tool_result":
    case "mcp_call":
    case "mcp_result":
      return (
        <div className="min-w-0">
          {event.body && (
            <p className="mt-2 whitespace-pre-wrap break-words text-body text-foreground">
              {event.body}
            </p>
          )}
          <DataView data={event.data ?? {}} />
        </div>
      );

    case "metric":
      return <DataChips data={event.data ?? {}} />;

    case "flag":
    case "finding":
      return (
        <div className="mt-2 space-y-2">
          {event.body && (
            <p className="whitespace-pre-wrap break-words text-body text-foreground">{event.body}</p>
          )}
          <Badge variant="info">agent-claimed</Badge>
        </div>
      );

    case "error":
      return (
        <p className="mt-2 whitespace-pre-wrap break-words text-body text-err">
          {event.body || event.title}
        </p>
      );

    case "status":
      return event.body ? (
        <p className="mt-2 whitespace-pre-wrap break-words text-body text-text-secondary">
          {event.body}
        </p>
      ) : null;

    // "unknown" and any future/unrecognized kind: render defensively, never throw.
    default:
      return (
        <pre className="mt-2 max-h-[28rem] overflow-auto whitespace-pre-wrap break-words rounded-md bg-deepest p-2 font-mono text-dense text-text-secondary">
          {event.body || JSON.stringify(event.data ?? {})}
        </pre>
      );
  }
}

/**
 * The one card used to render every AgentEvent kind. Dispatches on `event.kind`
 * ONLY — no `source_agent`/agent-name/span-name branching anywhere in this file.
 * Rendering the same event with a different `source_agent` must produce an
 * identical structural wrapper (asserted by a guard test).
 */
export function EventCard({
  event,
  onViewRaw,
  selected = false,
  onSelect,
  peerHovered = false,
  status,
}: {
  event: AgentEvent;
  onViewRaw: (event: AgentEvent) => void;
  /** Click-to-replay (Trace ↔ Terrain): true when this is the trace's selected event. */
  selected?: boolean;
  onSelect?: (event: AgentEvent) => void;
  /** Reverse hover link (Trace ↔ Terrain): true when a hovered terrain node's action targeted
   *  this event — a lighter emphasis than `selected`. */
  peerHovered?: boolean;
  /** Per-span BYOM-attribution status dot (iteration 2); omitted for non-attributable spans. */
  status?: AttributionSpanStatus;
}) {
  const meta = KIND_META[event.kind] ?? KIND_META.unknown;
  const color = eventColor(event.kind, event.role); // role-aware: user prompt gold vs assistant blue
  const label = displayLabel(event); // display-layer naming; raw title stays as the hover tooltip
  const Icon = meta.icon;
  const gateExplanation = permissionGateExplanation(event); // explain decision=reject/approve on hover
  const showPeerHover = peerHovered && !selected;
  return (
    <Card
      data-kind={event.kind}
      data-event-id={event.id}
      data-selected={selected || undefined}
      data-peer-hover={showPeerHover || undefined}
      className={cn(
        "border-l-2",
        color.accentClass,
        gateExplanation && "cursor-help",
        onSelect && "cursor-pointer",
        selected && "ring-1 ring-primary bg-primary/5",
        showPeerHover && "bg-primary/5",
      )}
      title={gateExplanation ?? undefined}
      onClick={onSelect ? () => onSelect(event) : undefined}
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      onKeyDown={
        onSelect
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                if (e.key === " ") e.preventDefault(); // avoid scrolling the page
                onSelect(event);
              }
            }
          : undefined
      }
    >
      <CardContent className="p-3">
        <div className="flex items-center gap-2">
          <StatusDot status={status} />
          <Icon className={cn("size-3.5 shrink-0", color.colorClass)} />
          <span
            className={cn("min-w-0 truncate text-caption font-semibold", color.colorClass)}
            title={event.title || undefined}
          >
            {label.title}
          </span>
          {(event.kind === "file_edit" || event.kind === "file_read") && event.title && (
            // Keep the adapter's "{tool} {path}" as a subtitle — the path is the payload.
            <span
              className="min-w-0 truncate font-mono text-caption text-text-tertiary"
              title={event.title}
            >
              {event.title}
            </span>
          )}
          <EventTime event={event} />
          <Badge variant="muted" className="shrink-0">
            {label.badge}
          </Badge>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 shrink-0 px-1.5"
            onClick={(e) => {
              e.stopPropagation(); // don't let the row-select swallow the raw-drilldown click
              onViewRaw(event);
            }}
          >
            View raw
          </Button>
        </div>
        <EventBody event={event} />
      </CardContent>
    </Card>
  );
}

/** A terminal `<pre>` used by both the command body and the folded output section. The command is
 *  shell-syntax-highlighted; the output is rendered with its real ANSI colours (VSCode-ish palette
 *  in both cases) so neither reads as flat white. */
function TerminalPre({
  body,
  kind,
}: {
  body: string;
  kind: "terminal_command" | "terminal_output";
}) {
  const content = body
    ? kind === "terminal_command"
      ? highlightShell(body)
      : renderAnsi(body)
    : "—";
  return (
    <pre className="mt-2 max-h-[28rem] overflow-auto whitespace-pre-wrap break-words rounded-md bg-deepest p-2 font-mono text-dense text-foreground">
      {content}
    </pre>
  );
}

/**
 * A shell action rendered as ONE cohesive card: the `terminal_command` on top and its paired
 * `terminal_output` as an attached, collapsed-by-default section below it (so the output is part
 * of the terminal span, not a separate sibling row). Agent-agnostic — the caller pairs command +
 * output by their shared id base, so this works identically for every harness. `output` is null
 * for a command that produced none.
 */
export function TerminalCard({
  command,
  output,
  outputExpanded,
  onToggleOutput,
  onViewRaw,
  selected = false,
  onSelect,
  peerHovered = false,
  status,
}: {
  command: AgentEvent;
  output: AgentEvent | null;
  outputExpanded: boolean;
  onToggleOutput: () => void;
  onViewRaw: (event: AgentEvent) => void;
  /** Click-to-replay (Trace ↔ Terrain): true when this is the trace's selected event. */
  selected?: boolean;
  onSelect?: (event: AgentEvent) => void;
  /** Reverse hover link (Trace ↔ Terrain): true when a hovered terrain node's action targeted
   *  this event — a lighter emphasis than `selected`. */
  peerHovered?: boolean;
  /** Per-span BYOM-attribution status dot (iteration 2); omitted for non-attributable spans. */
  status?: AttributionSpanStatus;
}) {
  const meta = KIND_META[command.kind] ?? KIND_META.unknown;
  const label = displayLabel(command); // display-layer naming; raw title stays as the hover tooltip
  const Icon = meta.icon;
  const gateExplanation = permissionGateExplanation(command);
  const showPeerHover = peerHovered && !selected;
  return (
    <Card
      data-kind={command.kind}
      data-event-id={command.id}
      data-selected={selected || undefined}
      data-peer-hover={showPeerHover || undefined}
      className={cn(
        "border-l-2",
        meta.accentClass,
        gateExplanation && "cursor-help",
        onSelect && "cursor-pointer",
        selected && "ring-1 ring-primary bg-primary/5",
        showPeerHover && "bg-primary/5",
      )}
      title={gateExplanation ?? undefined}
      onClick={onSelect ? () => onSelect(command) : undefined}
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      onKeyDown={
        onSelect
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                if (e.key === " ") e.preventDefault(); // avoid scrolling the page
                onSelect(command);
              }
            }
          : undefined
      }
    >
      <CardContent className="p-3">
        <div className="flex items-center gap-2">
          <StatusDot status={status} />
          <Icon className={cn("size-3.5 shrink-0", meta.colorClass)} />
          <span
            className={cn("min-w-0 truncate text-caption font-semibold", meta.colorClass)}
            title={command.title || undefined}
          >
            {label.title}
          </span>
          <EventTime event={command} />
          <Badge variant="muted" className="shrink-0">
            {label.badge}
          </Badge>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 shrink-0 px-1.5"
            onClick={(e) => {
              e.stopPropagation(); // don't let the row-select swallow the raw-drilldown click
              onViewRaw(command);
            }}
          >
            View raw
          </Button>
        </div>
        <TerminalPre body={command.body} kind="terminal_command" />

        {output && (
          <div className="mt-2 border-t border-border pt-2">
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleOutput();
                }}
                aria-expanded={outputExpanded}
                className="flex items-center gap-1.5 text-label uppercase text-text-tertiary hover:text-text-secondary"
              >
                {outputExpanded ? (
                  <ChevronDown className="size-3" />
                ) : (
                  <ChevronRight className="size-3" />
                )}
                output
              </button>
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto h-5 shrink-0 px-1.5"
                onClick={(e) => {
                  e.stopPropagation();
                  onViewRaw(output);
                }}
              >
                View raw
              </Button>
            </div>
            {outputExpanded && <TerminalPre body={output.body} kind="terminal_output" />}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
