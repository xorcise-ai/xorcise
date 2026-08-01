"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, Bot, Bug, ChevronDown, ChevronRight, Network, Radio, Server } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/components/ui/cn";
import type { AgentEvent, AgentEventKind } from "@/lib/api/types";
import type { InfraRow as InfraRowData } from "@/features/live-trace/terrain-fold";
import type { RunEventsMeta } from "./use-run-events";
import { KIND_META, displayLabel, formatEventTime } from "./kind-meta";
import { EventCard, EventTime, StatusDot, TerminalCard, type AttributionSpanStatus } from "./event-card";
import { RawDrillDownModal } from "./raw-drilldown-modal";
import { mergeAgentAndInfra } from "./replay-order";

/** The icon for an infra row, by its representative target id (Headscale / run-control / OTel
 *  collector / agent). Display-only. */
function infraIcon(targetId: string): typeof Bot {
  if (targetId === "agent") return Bot;
  if (targetId.startsWith("hs:") || targetId === "m:agent-hs") return Network;
  if (targetId.startsWith("rc:") || targetId === "m:agent-rc") return Server;
  return Radio; // collector / telemetry
}

/** A deterministic INFRA activity (join, brief, artifact, telemetry, …) as a compact, clickable
 *  Trace row — interleaved with the agent turns by receipt-time. Clicking it time-travels the
 *  terrain to that moment (via its synthetic `infra:<seq>` id). Dashed + muted so the infra plane
 *  reads apart from the agent's own spans. */
function InfraRow({
  row,
  selected,
  onSelect,
}: {
  row: InfraRowData;
  selected: boolean;
  onSelect?: (id: string) => void;
}) {
  const Icon = infraIcon(row.targetId);
  return (
    <button
      type="button"
      data-testid="infra-row"
      data-infra-id={row.id}
      data-selected={selected || undefined}
      onClick={() => onSelect?.(row.id)}
      className={cn(
        "flex w-full items-center gap-1.5 rounded-md border border-dashed border-border/60 px-2 py-1 text-left hover:bg-[rgba(255,255,255,0.04)]",
        selected && "bg-primary/5 ring-1 ring-primary",
      )}
    >
      <Icon className="size-3.5 shrink-0 text-text-tertiary" />
      <span className="truncate text-caption text-text-secondary">{row.label}</span>
      <span
        data-testid="event-time"
        className="ml-auto shrink-0 font-mono text-caption tabular-nums text-text-tertiary"
      >
        {formatEventTime(row.ts)}
      </span>
      <span className="shrink-0 text-label uppercase text-text-tertiary">
        infra
      </span>
    </button>
  );
}

/** Kinds folded to their title by default: a verbose `thinking` body collapses to its header,
 *  click to expand the full EventCard. (Terminal output is NOT here — it's merged into its
 *  command's `TerminalCard`; only an ORPHAN terminal_output falls back to this fold.) */
const FOLDED_KINDS = new Set<AgentEventKind>(["thinking"]);

/** The id base shared by a command and its output. Adapters emit a shell action as two events
 *  with a common base and a per-role suffix — Claude Code `<base>:tool` + `<base>:out`, OpenHands
 *  `<base>:cmd` + `<base>:out` — so stripping the last `:suffix` pairs them agent-agnostically. */
function idBase(id: string): string {
  const i = id.lastIndexOf(":");
  return i === -1 ? id : id.slice(0, i);
}

interface Turn {
  key: string;
  events: AgentEvent[];
}

interface AgentCard {
  key: string;
  groupKey: string;
  events: AgentEvent[];
  receivedTs: number;
  producerTs: number;
  signal: string;
  rawSeq: number;
}

const parseTime = (value: string | null | undefined) => (value ? Date.parse(value) : NaN);
const finiteOr = (value: number, fallback: number) =>
  Number.isFinite(value) ? value : fallback;

/** Build atomic display cards before mixing in infra. A terminal command and output must move as
 * one unit even if an infra timestamp falls between their producer timestamps. */
function agentCards(events: AgentEvent[]): AgentCard[] {
  const outputByBase = new Map(
    events
      .filter((event) => event.kind === "terminal_output")
      .map((event) => [idBase(event.id), event] as const),
  );
  const commandBases = new Set(
    events
      .filter((event) => event.kind === "terminal_command")
      .map((event) => idBase(event.id)),
  );
  const cards: AgentCard[] = [];
  for (const event of events) {
    if (event.kind === "terminal_output" && commandBases.has(idBase(event.id))) continue;
    const output =
      event.kind === "terminal_command" ? outputByBase.get(idBase(event.id)) : undefined;
    const members = output ? [event, output] : [event];
    const received = parseTime(event.received_at);
    const producer = parseTime(event.ts);
    const producerTs = finiteOr(producer, Number.NEGATIVE_INFINITY);
    cards.push({
      key: event.id,
      groupKey: event.group_id ?? idBase(event.id),
      events: members,
      // Old/in-memory API payloads may not have received_at. Falling back to producer time keeps
      // them renderable, while persisted runs always use the shared server-side clock.
      receivedTs: finiteOr(received, producerTs),
      producerTs,
      signal: event.raw_ref.signal ?? "trace",
      rawSeq: event.raw_ref.raw_seq,
    });
  }
  return cards;
}

/** A folded row (thinking / terminal output): collapsed to its title by default, expands to
 * the full EventCard (so the same kind-driven dispatcher renders its body). Kind-driven only —
 * the icon/color come from the event's own KIND_META, never from `source_agent`. */
function FoldableRow({
  event,
  expanded,
  onToggle,
  onViewRaw,
  selected = false,
  onSelect,
  peerHovered = false,
  status,
}: {
  event: AgentEvent;
  expanded: boolean;
  onToggle: () => void;
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
  const label = displayLabel(event); // display-layer naming; raw title stays as the hover tooltip
  const Icon = meta.icon;
  const showPeerHover = peerHovered && !selected;
  return (
    <div
      data-event-id={event.id}
      data-selected={selected || undefined}
      data-peer-hover={showPeerHover || undefined}
    >
      <button
        type="button"
        onClick={() => {
          onToggle();
          onSelect?.(event);
        }}
        aria-expanded={expanded}
        className={cn(
          "flex w-full items-center gap-1.5 rounded-md px-1 py-1 text-left hover:bg-[rgba(255,255,255,0.04)]",
          selected && "ring-1 ring-primary bg-primary/5",
          showPeerHover && "bg-primary/5",
        )}
      >
        {expanded ? (
          <ChevronDown className="size-3 shrink-0 text-text-tertiary" />
        ) : (
          <ChevronRight className="size-3 shrink-0 text-text-tertiary" />
        )}
        <StatusDot status={status} />
        <Icon className={cn("size-3.5 shrink-0", meta.colorClass)} />
        <span
          className={cn("truncate text-caption font-semibold", meta.colorClass)}
          title={event.title || undefined}
        >
          {label.title}
        </span>
        <EventTime event={event} />
      </button>
      {expanded && (
        <div className="mt-1">
          <EventCard event={event} onViewRaw={onViewRaw} />
        </div>
      )}
    </div>
  );
}

/**
 * Renders a run's AgentEvent stream as grouped conversational turns. Agent-
 * agnostic: every event is dispatched to `EventCard` by `event.kind` only.
 * `thinking` folds to its title by default; a `terminal_command` + its paired
 * `terminal_output` merge into one `TerminalCard` (output collapsed below the
 * command); `metric`/`unknown` (kind-meta group "debug") are hidden unless the
 * Debug toggle is on.
 */
export function ReplayTimeline({
  runId,
  events,
  meta,
  selectedEventId = null,
  onSelectEvent,
  hoveredEventIds,
  attributedActionIds,
  consideredIds,
  attributing = false,
  infraRows,
  fill = false,
}: {
  runId: string;
  events: AgentEvent[];
  meta?: RunEventsMeta | null;
  /** Fill the parent's height with a SINGLE internal scroller (side-by-side split), instead of the
   *  self-bounded `max-h-[70vh]` used in the stacked layout — avoids nesting two scrollbars when the
   *  parent is already a bounded, filling container. The Debug toggle stays pinned above the scroll. */
  fill?: boolean;
  /** Click-to-replay (Trace ↔ Terrain): the event whose terrain action is highlighted on the map.
   *  May be an agent span id OR a synthetic `infra:<seq>` id (an infra row). */
  selectedEventId?: string | null;
  /** Passing `null` clears the selection (returns to live) — the return-to-live pill uses this to
   *  exit terrain time-travel; a span id selects that event. */
  onSelectEvent?: (id: string | null) => void;
  /** Deterministic infra activities (join, brief, artifact, telemetry, …), interleaved with the
   *  agent turns by receipt-time and clickable for time-travel. Derived by `deriveInfraRows`. */
  infraRows?: InfraRowData[];
  /** Reverse hover link (Trace ↔ Terrain): events whose terrain action targeted the currently
   *  hovered map node — highlighted at a lighter emphasis than `selectedEventId`. */
  hoveredEventIds?: string[];
  /** Per-span attribution status (iteration 2): ids whose action produced a terrain edge. */
  attributedActionIds?: Set<string>;
  /** Ids the model has considered (attributed, applicable or not) — the "analyzed" set. */
  consideredIds?: Set<string>;
  /** True while a catch-up batch is in flight — unattributed action-spans show "attributing…". */
  attributing?: boolean;
}) {
  const [debug, setDebug] = useState(false);
  const hoveredEventIdSet = useMemo(
    () => new Set(hoveredEventIds ?? []),
    [hoveredEventIds],
  );
  /** A span's attribution status, or undefined for a non-attributable (conversation/debug) span.
   *  completed→edge = "action"; considered-but-empty = "analyzed"; else in-progress vs pending. */
  const attrStatus = (event: AgentEvent): AttributionSpanStatus | undefined => {
    if ((KIND_META[event.kind] ?? KIND_META.unknown).group !== "action") return undefined;
    if (attributedActionIds?.has(event.id)) return "action";
    if (consideredIds?.has(event.id)) return "analyzed";
    return attributing ? "in-progress" : "pending";
  };
  /** Status for a card that MERGES several agent-events (e.g. a terminal command + its output). The
   *  attributor attaches the terrain action to whichever sub-event carries it — for terminals that's
   *  usually the OUTPUT — so the card lights green if ANY of its constituent events is attributed.
   *  Gated on the primary (first) event's kind; nulls (e.g. a missing output) are ignored. */
  const attrStatusForSpan = (
    primary: AgentEvent,
    ...rest: (AgentEvent | null | undefined)[]
  ): AttributionSpanStatus | undefined => {
    if ((KIND_META[primary.kind] ?? KIND_META.unknown).group !== "action") return undefined;
    const ids = [primary, ...rest].filter((e): e is AgentEvent => !!e).map((e) => e.id);
    if (ids.some((id) => attributedActionIds?.has(id))) return "action";
    if (ids.some((id) => consideredIds?.has(id))) return "analyzed";
    return attributing ? "in-progress" : "pending";
  };
  /** Click-to-time-travel is enabled ONLY for spans that produced a terrain action ("action").
   *  A span with no terrain update has no fold index to rewind to — selecting it would fall back to
   *  the latest fold and yank the map to the fully-completed state, so such rows are inert. */
  const selectHandlerFor = (status: AttributionSpanStatus | undefined) =>
    onSelectEvent && status === "action" ? (e: AgentEvent) => onSelectEvent(e.id) : undefined;
  const [expandedFolded, setExpandedFolded] = useState<Set<string>>(new Set());
  const [rawEvent, setRawEvent] = useState<AgentEvent | null>(null);

  const visible = useMemo(
    () =>
      debug
        ? events
        : events.filter((e) => (KIND_META[e.kind] ?? KIND_META.unknown).group !== "debug"),
    [events, debug],
  );
  // Fix the agent narrative in producer order, then merge infra using receipt as one-way evidence.
  // This preserves infra's server-clock interleaving without mistaking a delayed export for a late
  // event. Merge before visual grouping so infra can still split Claude's whole-session group.
  type Entry =
    | { kind: "turn"; ts: number; turn: Turn }
    | { kind: "infra"; ts: number; row: InfraRowData };
  const entries = useMemo<Entry[]>(() => {
    const cards = agentCards(visible).sort((a, b) => {
      if (a.producerTs !== b.producerTs) return a.producerTs - b.producerTs;
      const signal = a.signal.localeCompare(b.signal);
      if (signal !== 0) return signal;
      if (a.rawSeq !== b.rawSeq) return a.rawSeq - b.rawSeq;
      return a.key.localeCompare(b.key);
    });
    const atomic = mergeAgentAndInfra(cards, infraRows ?? [], {
      agentReceipt: (card) => card.events[0]?.received_at,
      infraTime: (row) => row.ts,
      infraSequence: (row) => row.seq,
    });

    const merged: Entry[] = [];
    let previousGroup: string | null = null;
    for (const item of atomic) {
      if (item.kind === "infra") {
        const row = item.infra;
        merged.push({
          kind: "infra",
          ts: finiteOr(parseTime(row.ts), Number.POSITIVE_INFINITY),
          row,
        });
        previousGroup = null; // infra deliberately splits a long visual agent turn
        continue;
      }
      const card = item.agent;
      const previous = merged.at(-1);
      if (previous?.kind === "turn" && previousGroup === card.groupKey) {
        previous.turn.events.push(...card.events);
      } else {
        merged.push({
          kind: "turn",
          ts: card.receivedTs,
          turn: { key: card.key, events: [...card.events] },
        });
      }
      previousGroup = card.groupKey;
    }
    return merged;
  }, [visible, infraRows]);

  function toggleFolded(id: string) {
    setExpandedFolded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Follow-the-tail behavior: a bounded, scrollable trace pane that stays pinned to the newest
  // event while the operator is already at the bottom, but stops chasing (and offers a "jump to
  // latest") the moment they scroll up to read history.
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLOListElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  // Mirror `atBottom` into a ref so the ResizeObserver below (subscribed once) reads the CURRENT
  // value without re-subscribing on every poll.
  const atBottomRef = useRef(true);
  useEffect(() => {
    atBottomRef.current = atBottom;
  }, [atBottom]);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (el) setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    setAtBottom(true);
  }, []);

  // Return to live: clear the selection (un-freezes the terrain time-travel, owned by RunLive) AND
  // re-pin to the newest event. Both halves in one action so inspecting a span never strands the
  // operator on a frozen view that only a page refresh could recover.
  const returnToLive = useCallback(() => {
    onSelectEvent?.(null);
    scrollToBottom();
  }, [onSelectEvent, scrollToBottom]);

  // Re-pin after each batch of agent OR infra entries, but only when the operator hasn't scrolled
  // away. Depending only on visible.length missed infra-only polling updates.
  useLayoutEffect(() => {
    if (atBottom) scrollToBottom();
  }, [entries.length, atBottom, scrollToBottom]);

  // SIZE-driven re-pin — the load-bearing fix for follow-tail. Entry COUNT is not enough: a
  // same-group Claude burst appends many spans into ONE turn entry, and a terminal_output merges
  // into its already-rendered command card — both grow the content height with `entries.length`
  // unchanged, so the count-keyed effect above never fires and the tail slides off screen. Observing
  // the content's actual size catches every growth (grouped turns, merged output, async images,
  // expand/collapse) uniformly. jsdom has no ResizeObserver → guarded; `hasEntries` re-runs the
  // attach once the <ol> mounts (the empty state renders a <p> instead, so the ref starts null).
  const hasEntries = entries.length > 0;
  useEffect(() => {
    const content = contentRef.current;
    if (!content || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (atBottomRef.current) scrollToBottom();
    });
    ro.observe(content);
    return () => ro.disconnect();
  }, [hasEntries, scrollToBottom]);

  // Timeline → Trace: when a selection lands (e.g. a waterfall bar click), bring that event's
  // card into view and suppress follow-tail so the re-pin above doesn't yank the view back down.
  useEffect(() => {
    if (!selectedEventId) return;
    const root = scrollRef.current;
    if (!root) return;
    // Ids can contain CSS-hostile characters (":", "="); CSS.escape may be absent in old jsdom.
    const esc =
      typeof CSS !== "undefined" && typeof CSS.escape === "function"
        ? CSS.escape
        : (s: string) => s.replace(/["\\]/g, "\\$&");
    const el = root.querySelector<HTMLElement>(`[data-event-id="${esc(selectedEventId)}"]`);
    if (!el) return;
    setAtBottom(false);
    el.scrollIntoView?.({ block: "center" });
  }, [selectedEventId]);

  return (
    <div className={fill ? "flex min-h-0 flex-1 flex-col" : "space-y-3"}>
      <div className={`flex items-center justify-end gap-2 ${fill ? "mb-3 shrink-0" : ""}`}>
        {meta?.fallback && (
          <span className="mr-auto text-label uppercase text-text-tertiary">
            generic renderer
          </span>
        )}
        <Bug className="size-3 text-text-tertiary" />
        <span className="text-label uppercase text-text-tertiary">Debug</span>
        <Switch checked={debug} onChange={setDebug} label="Show debug events (metric, unknown)" />
      </div>

      <div className={fill ? "relative min-h-0 flex-1" : "relative"}>
        <div
          ref={scrollRef}
          onScroll={onScroll}
          data-testid="trace-scroll"
          className={
            fill ? "h-full overflow-y-auto pr-1" : "max-h-[70vh] overflow-y-auto pr-1"
          }
        >
          {entries.length === 0 ? (
            <p className="text-body text-text-secondary">Waiting for trace events…</p>
          ) : (
            <ol ref={contentRef} className="space-y-3">
              {entries.map((entry) => {
                if (entry.kind === "infra") {
                  return (
                    <li key={entry.row.id} data-testid="infra-turn" className="space-y-2">
                      <InfraRow
                        row={entry.row}
                        selected={entry.row.id === selectedEventId}
                        onSelect={onSelectEvent}
                      />
                    </li>
                  );
                }
                const turn = entry.turn;
                // Pair each terminal_command with its terminal_output (shared id base) so they
                // render as one TerminalCard; the output is then not drawn as a separate row.
                const outputByBase = new Map(
                  turn.events
                    .filter((e) => e.kind === "terminal_output")
                    .map((e) => [idBase(e.id), e] as const),
                );
                const commandBases = new Set(
                  turn.events
                    .filter((e) => e.kind === "terminal_command")
                    .map((e) => idBase(e.id)),
                );
                return (
                  <li key={turn.key} data-testid="replay-turn" className="space-y-2">
                    {turn.events.map((event) => {
                      if (event.kind === "terminal_output") {
                        // Absorbed into its command's card; only an ORPHAN output renders (folded).
                        if (commandBases.has(idBase(event.id))) return null;
                        const status = attrStatus(event);
                        return (
                          <FoldableRow
                            key={event.id}
                            event={event}
                            expanded={expandedFolded.has(event.id)}
                            onToggle={() => toggleFolded(event.id)}
                            onViewRaw={setRawEvent}
                            selected={event.id === selectedEventId}
                            onSelect={selectHandlerFor(status)}
                            peerHovered={hoveredEventIdSet.has(event.id)}
                            status={status}
                          />
                        );
                      }
                      if (event.kind === "terminal_command") {
                        const output = outputByBase.get(idBase(event.id)) ?? null;
                        const status = attrStatusForSpan(event, output);
                        return (
                          <TerminalCard
                            key={event.id}
                            command={event}
                            output={output}
                            outputExpanded={!!output && expandedFolded.has(output.id)}
                            onToggleOutput={() => output && toggleFolded(output.id)}
                            onViewRaw={setRawEvent}
                            selected={event.id === selectedEventId}
                            onSelect={selectHandlerFor(status)}
                            peerHovered={hoveredEventIdSet.has(event.id)}
                            status={status}
                          />
                        );
                      }
                      if (FOLDED_KINDS.has(event.kind)) {
                        const status = attrStatus(event);
                        return (
                          <FoldableRow
                            key={event.id}
                            event={event}
                            expanded={expandedFolded.has(event.id)}
                            onToggle={() => toggleFolded(event.id)}
                            onViewRaw={setRawEvent}
                            selected={event.id === selectedEventId}
                            onSelect={selectHandlerFor(status)}
                            peerHovered={hoveredEventIdSet.has(event.id)}
                            status={status}
                          />
                        );
                      }
                      const status = attrStatus(event);
                      return (
                        <EventCard
                          key={event.id}
                          event={event}
                          onViewRaw={setRawEvent}
                          selected={event.id === selectedEventId}
                          onSelect={selectHandlerFor(status)}
                          peerHovered={hoveredEventIdSet.has(event.id)}
                          status={status}
                        />
                      );
                    })}
                  </li>
                );
              })}
            </ol>
          )}
        </div>

        {/* Recovery pill: while a span is selected it reads "Return to live" — clicking it also
            exits the terrain time-travel (clears the selection) — otherwise it's the plain
            "Jump to latest" scroll re-pin. Both routes call `returnToLive` (clearing a null
            selection is a no-op), so one control always rejoins the live tail without a refresh. */}
        {!atBottom &&
          (selectedEventId ? (
            <button
              type="button"
              onClick={returnToLive}
              className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-primary/40 bg-card px-3 py-1.5 text-caption text-primary shadow-lg hover:border-primary"
            >
              <Radio className="size-3" />
              Return to live
            </button>
          ) : (
            <button
              type="button"
              onClick={returnToLive}
              className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-caption text-foreground shadow-lg hover:border-[rgba(255,255,255,0.14)]"
            >
              <ArrowDown className="size-3" />
              Jump to latest
            </button>
          ))}
      </div>

      <RawDrillDownModal
        runId={runId}
        event={rawEvent}
        open={rawEvent != null}
        onClose={() => setRawEvent(null)}
      />
    </div>
  );
}
