"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { Maximize2, Minimize2, Minus, Plus, Radio } from "lucide-react";
import { foldIndexForEvent, foldTerrain, probingPathEdgeIds, pulseIdsForIndex, type FoldedNode } from "./terrain-fold";
import { layoutTerrainV2, type LaidOutEdge, type LaidOutGroup, type LaidOutNode } from "./terrain-layout";
import { edgeColor, groupStyle, nodeColor, otelActive, T } from "./terrain-colors";
import { useRunTerrain } from "./use-run-terrain";
import type { ResolvedTerrainV2 } from "@/lib/api/types";

/**
 * Terrain map v2 — a spatial map rendered as hand-rolled inline SVG (no graph library), drawing the
 * folded authored graph. The color scheme lives in `terrain-colors.ts` (pure, unit-tested):
 *
 *   node   a DARK disc with a colored ring border + a small colored inner dot (state color):
 *          grey = unknown · yellow = the agent node (only it) · blue = discovered · green =
 *          enumerated · red = the objective. The objective is the ONLY node with an ⊗ (X) instead
 *          of the inner dot; it stays X and turns red→green. Completion is the green color, no dot.
 *   group  XORCISE-infra = blue · agent workspace = yellow · mission segment = grey → green once
 *          enumerated. HARD OTEL GATE: the mission plane stays grey until the agent emits OTel.
 *   edge   currently-probing (pulsed) = yellow · other active = blue · inactive = border
 *
 * TIME-TRAVEL: clicking a trace span (`selectedEventId`) rewinds the map to the cumulative state as
 * of that span (`foldIndexForEvent`) and marks just that span's delta (`pulseIdsForIndex`) with the
 * amber `.tm-ring-amber` ring — kept a distinct hue from the state palette so "what just changed"
 * never reads as a state color. The default (unselected/live) view instead pulses the SINGLE node
 * the agent's last attributed action landed on (`hotNodeId`) with the yellow in-progress halo — not
 * every discovered node. Selection is PROP-DRIVEN (owned by RunLive) — `k` derives from
 * `selectedEventId` every render, so a live poll updating "latest" never yanks a selected rewind
 * forward; nothing here resets the selection.
 *
 * All positions come from the deterministic layout in `terrain-layout.ts`. The view fits the whole
 * graph on load; zoom/pan is a CSS transform (carried from the v1 renderer). Wheel zoom is GATED so
 * the map never hijacks page scroll: Ctrl/⌘+wheel always zooms, plain wheel zooms only after the
 * map is engaged (pointerdown/focus, released on blur/Escape) — see the native wheel effect.
 */

const AMBER = "var(--color-primary)";

/** Clamp a zoom factor to the pan/zoom bounds. Module-scope so `fitToView` can be a STABLE
 *  callback (the ResizeObserver + auto-fit-follow effects re-run/re-subscribe off its identity). */
const clampZoom = (z: number) => Math.min(3, Math.max(0.2, z));

/** The drawn service id an id collapses onto: the prefix before its first `:` (an endpoint like
 *  `web:login` collapses onto `web`). Mirrors `endpointParentId` in terrain-layout so the hot-node
 *  halo lands on the visible parent when the last action targeted a collapsed endpoint. */
function drawnParentId(id: string): string {
  const i = id.indexOf(":");
  return i === -1 ? id : id.slice(0, i);
}

// Node labels are authored free text — short ids like `web` up to a full objective sentence. A
// single centered, unbounded <text> spills sideways UNDER the adjacent node's label when the label
// is wider than the node's horizontal slot (`slotW` = bandW / node-count). Clamp each label to its
// slot: greedy word-wrap to a per-line character budget, HARD-break a token longer than the budget,
// and clamp to two lines with an ellipsis. The full label is preserved in a <title> when truncated.
const LABEL_CHAR_W = 6.3; // mono advance ≈ 0.6em at fontSize 10.5
const LABEL_LINE_H = 11;
const LABEL_MAX_LINES = 2;
const LABEL_GUTTER = 10; // px kept clear each side so a line never touches the neighbour's slot

function clampLabel(label: string, slotW: number): { lines: string[]; truncated: boolean } {
  const maxChars = Math.max(4, Math.floor((slotW - LABEL_GUTTER) / LABEL_CHAR_W));
  const lines: string[] = [];
  let cur = "";
  const flush = () => {
    if (cur) {
      lines.push(cur);
      cur = "";
    }
  };
  for (const word of label.split(/\s+/).filter(Boolean)) {
    let w = word;
    // hard-break a single token longer than one line can hold (e.g. a long URL/path)
    while (w.length > maxChars) {
      flush();
      lines.push(w.slice(0, maxChars));
      w = w.slice(maxChars);
    }
    if (!cur) cur = w;
    else if ((cur + " " + w).length <= maxChars) cur += " " + w;
    else {
      flush();
      cur = w;
    }
  }
  flush();

  if (lines.length <= LABEL_MAX_LINES) return { lines, truncated: false };
  // more than two lines — keep the first two and ellipsize the second
  const kept = lines.slice(0, LABEL_MAX_LINES);
  const last = kept[LABEL_MAX_LINES - 1];
  const trimmed = last.length > maxChars - 1 ? last.slice(0, Math.max(1, maxChars - 1)) : last;
  kept[LABEL_MAX_LINES - 1] = trimmed.replace(/\s+$/, "") + "…";
  return { lines: kept, truncated: true };
}

function NodeMark({
  ln,
  pulsed,
  hot,
  color,
  onEnter,
  onLeave,
}: {
  ln: LaidOutNode;
  pulsed: boolean;
  /** True for the SINGLE node the agent's last attributed action landed on (live view only) — it
   *  alone gets the pulsing "in-progress" halo. Previously EVERY discovered node pulsed, which read
   *  as the whole map being active at once. */
  hot: boolean;
  /** Fill color, precomputed by the parent via `nodeColor` (needs the node's group kind + OTel). */
  color: string;
  onEnter: () => void;
  onLeave: () => void;
}) {
  const { fnode, r } = ln;
  const { node, state } = fnode;
  // Old-XORCISE-terrain node grammar: a DARK disc with a colored RING border + a small colored
  // inner dot (both in the state color). The objective replaces the dot with a crossed ⊗ (X) —
  // and it is the ONLY node that gets the X. "Unknown" (grey) uses a dashed ring; completion is
  // conveyed by the green state color, not a separate dot.
  const dashed = color === T.grey;
  const dotR = Math.max(2, r * 0.28);
  const x = r * 0.55;

  return (
    <g
      transform={`translate(${ln.x}, ${ln.y})`}
      data-node-id={node.id}
      data-node-state={state}
      data-node-objective={node.objective}
      onPointerEnter={onEnter}
      onPointerLeave={onLeave}
      style={{ cursor: "help" }}
    >
      {/* time-travel delta highlight — always amber, distinct from the state palette */}
      {pulsed && (
        <circle r={r} fill="none" stroke={AMBER} className="tm-ring-amber" data-node-pulse="true" />
      )}
      {/* "in progress" halo — ONLY on the single node the agent's last attributed action landed on
          (not every discovered node); matches the discovered state color */}
      {hot && <circle r={r} fill="none" stroke={T.blue} className="tm-pulse-yellow" data-node-hot="true" />}
      {/* body: dark disc + colored ring border (the state color lives on the stroke, not the fill) */}
      <circle
        r={r}
        data-node-fill
        fill="var(--color-card)"
        stroke={color}
        strokeWidth={1.5}
        strokeDasharray={dashed ? "3 3" : undefined}
      />
      {node.objective ? (
        <g stroke={color} strokeWidth={1.5} strokeLinecap="round" data-objective-glyph="x">
          <line x1={-x} y1={-x} x2={x} y2={x} />
          <line x1={x} y1={-x} x2={-x} y2={x} />
        </g>
      ) : (
        <circle r={dotR} fill={color} data-node-dot="true" />
      )}
      {/* label — clamped to the node's slot so a long label never overlaps the neighbour's; the
          full text lives in <title> when the visible label was truncated */}
      {(() => {
        const { lines, truncated } = clampLabel(node.label, ln.slotW);
        return (
          <g data-node-label>
            {truncated && <title>{node.label}</title>}
            {lines.map((line, i) => (
              <text
                key={i}
                y={r + 13 + i * LABEL_LINE_H}
                textAnchor="middle"
                fill="var(--color-foreground)"
                fontSize={10.5}
                fontFamily="var(--font-mono)"
              >
                {line}
              </text>
            ))}
          </g>
        );
      })()}
    </g>
  );
}

function GroupBox({
  lg,
  pulsed,
  otel,
  enumerated,
}: {
  lg: LaidOutGroup;
  pulsed: boolean;
  otel: boolean;
  /** true when every member node of this (mission) group has been enumerated (completed). */
  enumerated: boolean;
}) {
  const { fgroup, x, y, w, h } = lg;
  const discovered = fgroup.discovered;
  // infra=blue, agent=yellow, mission segment=grey (unknown) → blue (discovered) → green (all
  // nodes enumerated). NOTE: the group box always uses its STATE color — never the amber pulse —
  // so a network never reads as yellow (only the agent workspace is yellow). `pulsed` keeps a
  // subtle just-changed thickness cue, no color change.
  const { stroke, solid } = groupStyle(fgroup, otel, enumerated);
  return (
    <g data-group-id={fgroup.group.id} data-group-discovered={discovered}>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={4}
        fill="none"
        stroke={stroke}
        strokeWidth={pulsed ? 1.5 : 1}
        strokeDasharray={solid ? undefined : "4 4"}
        strokeOpacity={solid ? 1 : 0.7}
      />
      <text
        x={x + 8}
        y={y + 15}
        fill="var(--color-muted-foreground)"
        fontSize={9}
        fontFamily="var(--font-mono)"
        style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}
      >
        {fgroup.group.label}
      </text>
    </g>
  );
}

function EdgeLine({
  le,
  pulsed,
  note,
}: {
  le: LaidOutEdge;
  pulsed: boolean;
  /** The route/action actually used on this edge (most-recent targeting update's note, up to the
   *  current fold boundary) — rendered PERSISTENTLY at the edge's midpoint by `EdgeNoteTooltip`
   *  below, not hover-gated. Kept here only for the `data-edge-note` debug attribute. */
  note: string | null;
}) {
  const active = le.fedge.active;
  // currently-probing (pulsed) = yellow · other active = blue · inactive = border.
  const stroke = edgeColor(active, pulsed) ?? "var(--color-border)";
  return (
    <line
      x1={le.x1}
      y1={le.y1}
      x2={le.x2}
      y2={le.y2}
      stroke={stroke}
      strokeWidth={pulsed ? 1.75 : active ? 1.25 : 1}
      strokeDasharray={active ? "1.5 3.5" : undefined}
      strokeLinecap={active ? "round" : undefined}
      className={pulsed ? "tm-flow" : undefined}
      data-edge-id={le.fedge.edge.id}
      data-edge-active={active}
      data-edge-note={note ?? undefined}
    />
  );
}

/** The route/action actually used on this edge, rendered PERSISTENTLY at the edge's midpoint,
 *  drawn on top, non-interactive — every acted-on edge shows what happened right on the line
 *  (not hover-gated, and not a bottom-of-panel caption). Mirrors the old map's
 *  `ConnectionPopover`. */
// Edge notes wrap at ~20 mono chars (~120px) so adjacent edges' annotations don't overlap
// horizontally. Height is CLAMPED to the first two lines so a long note can't grow tall and clutter
// the map; the full text is truncated with a fade and expands smoothly on hover (see .tm-edge-note).
const EDGE_NOTE_MAX_CHARS = 20;
const EDGE_NOTE_LINE_H = 11; // px per wrapped line in the HTML box
const EDGE_NOTE_PAD_Y = 8; // 6px padding + the box's 1px border top and bottom. The border
                           // was never counted, so every note was clipped ~2px short of its
                           // last line (the box is border-box, and .tm-edge-note draws a 1px
                           // border on top of its 3px vertical padding).
const EDGE_NOTE_COLLAPSED_LINES = 2;

function EdgeNoteTooltip({ le, note }: { le: LaidOutEdge; note: string }) {
  const midX = (le.x1 + le.x2) / 2;
  const midY = (le.y1 + le.y2) / 2;
  const lines = wrapText(note, EDGE_NOTE_MAX_CHARS);
  const truncated = lines.length > EDGE_NOTE_COLLAPSED_LINES;
  const collapsedH =
    Math.min(lines.length, EDGE_NOTE_COLLAPSED_LINES) * EDGE_NOTE_LINE_H + EDGE_NOTE_PAD_Y;
  const fullH = lines.length * EDGE_NOTE_LINE_H + EDGE_NOTE_PAD_Y;
  // A line of slack over the wrap ESTIMATE so the browser's real wrapping never clips the expanded
  // box (the estimate can be off by a line at this width).
  const expandedH = fullH + EDGE_NOTE_LINE_H;
  const boxW = EDGE_NOTE_MAX_CHARS * 5.4 + 14;
  // The foreignObject is sized to the EXPANDED height so a hover-expanded note is never clipped by
  // the SVG box; the inner div starts clamped to `collapsedH` and grows to `expandedH` purely in CSS
  // (so the full text always lives in the DOM / is accessible). Anchored so the COLLAPSED box is
  // centred on the edge midpoint; expansion grows downward.
  return (
    <foreignObject
      x={midX - boxW / 2}
      y={midY - collapsedH / 2}
      width={boxW}
      height={expandedH + 2}
      pointerEvents="none"
      data-testid="edge-note-tooltip"
      data-truncated={truncated}
    >
      <div
        className={`tm-edge-note${truncated ? " tm-edge-note--truncated" : ""}`}
        style={
          {
            "--tm-note-collapsed": `${collapsedH}px`,
            "--tm-note-full": `${expandedH}px`,
          } as CSSProperties
        }
      >
        {note}
      </div>
    </foreignObject>
  );
}

/** Greedy word-wrap at ~maxLen chars/line, so a long authored description doesn't overflow the
 *  tooltip box. Pure text layout — no measurement, matches the fixed-width mono font closely
 *  enough for a small hover popover. */
function wrapText(text: string, maxLen = 44): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    if (cur.length === 0) {
      cur = w;
    } else if ((cur + " " + w).length <= maxLen) {
      cur += " " + w;
    } else {
      lines.push(cur);
      cur = w;
    }
  }
  if (cur.length > 0) lines.push(cur);
  return lines;
}

/** Hover popover (Change 2, extended) — a node's authored `description` (when present) AND its
 *  collapsed endpoint routes (when present), drawn on top, non-interactive. A node with only a
 *  description shows just that; a service node with routes still shows routes (plus its
 *  description if it has one); a node with neither renders nothing. Mirrors the old map's
 *  `ConnectionPopover`. */
function NodeTooltip({ ln }: { ln: LaidOutNode }) {
  const description = ln.fnode.node.description;
  const routes = ln.routes ?? [];
  const descLines = description ? wrapText(description) : [];
  if (descLines.length === 0 && routes.length === 0) return null;

  const lineH = 13;
  const maxLineLen = Math.max(0, ...descLines.map((l) => l.length), ...routes.map((r) => r.label.length));
  const boxW = Math.max(88, maxLineLen * 5.7 + 20);
  const headerRows = routes.length > 0 ? 1 : 0;
  const boxH = (descLines.length + headerRows + routes.length) * lineH + 16;

  return (
    <g
      transform={`translate(${ln.x}, ${ln.y + ln.r + 8})`}
      pointerEvents="none"
      data-testid="node-tooltip"
    >
      <rect
        x={-boxW / 2}
        y={0}
        width={boxW}
        height={boxH}
        rx={4}
        fill="var(--color-popover)"
        stroke="var(--color-border)"
      />
      {descLines.map((line, i) => (
        <text
          key={`desc-${i}`}
          x={0}
          y={12 + i * lineH}
          textAnchor="middle"
          fill="var(--color-popover-foreground)"
          fontSize={9}
          fontFamily="var(--font-mono)"
        >
          {/* trailing space on all but the last line — adjacent SVG <text> siblings have no
              inherent gap, so textContent (and assistive tech) would otherwise run wrapped
              words together, e.g. "…SSRF-capable /fetchendpoint." */}
          {i < descLines.length - 1 ? `${line} ` : line}
        </text>
      ))}
      {routes.length > 0 && (
        <g data-testid="routes-tooltip">
          <text
            x={0}
            y={12 + descLines.length * lineH}
            textAnchor="middle"
            fill="var(--color-muted-foreground)"
            fontSize={7.5}
            fontFamily="var(--font-mono)"
            style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}
          >
            routes
          </text>
          {routes.map((r, i) => (
            <text
              key={r.id}
              x={0}
              y={12 + (descLines.length + 1 + i) * lineH}
              textAnchor="middle"
              fill="var(--color-popover-foreground)"
              fontSize={9}
              fontFamily="var(--font-mono)"
            >
              {r.label}
            </text>
          ))}
        </g>
      )}
    </g>
  );
}

const LEGEND: { label: string; color: string; edge?: boolean; dash?: boolean }[] = [
  { label: "unknown", color: T.grey, dash: true },
  { label: "agent", color: T.yellow },
  { label: "discovered", color: T.blue },
  { label: "enumerated", color: T.green },
  { label: "objective (open)", color: T.red },
  { label: "authored edge", color: "var(--color-border)", edge: true },
  { label: "active edge", color: T.blue, edge: true, dash: true },
  { label: "probing edge", color: T.yellow, edge: true, dash: true },
];

function Legend() {
  // Collapsible: the eight-row key is handy on first read but then just occupies the corner of a
  // graph the operator wants to see. Minimised, it's a single "Legend" pill one click from the key.
  const [open, setOpen] = useState(true);
  return (
    <div className="absolute right-2 top-2 rounded-md border border-border bg-card/90 backdrop-blur">
      <button
        type="button"
        aria-label={open ? "Minimise legend" : "Expand legend"}
        aria-expanded={open}
        // stop the pointerdown reaching the viewport, which would otherwise start a map pan.
        onPointerDown={(e) => e.stopPropagation()}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-3 px-2.5 py-1.5 text-label uppercase text-text-tertiary hover:text-text-secondary"
      >
        Legend
        {open ? <Minus className="size-2.5" aria-hidden /> : <Plus className="size-2.5" aria-hidden />}
      </button>
      {open && (
        <ul className="space-y-1 px-2.5 pb-2">
          {LEGEND.map((l) => (
            <li key={l.label} className="flex items-center gap-1.5 text-caption text-text-secondary">
              {l.edge ? (
                <span
                  className="inline-block h-0 w-3.5 border-t"
                  style={{ borderColor: l.color, borderStyle: l.dash ? "dashed" : "solid" }}
                />
              ) : (
                <span
                  className="inline-block size-2 rounded-full border"
                  style={{
                    borderColor: l.color,
                    borderStyle: l.dash ? "dashed" : "solid",
                    background: l.dash ? "transparent" : l.color,
                  }}
                />
              )}
              {l.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The run-scoped terrain map: fetches the run's resolved v2 terrain and renders it.
 *  All drawing lives in `TerrainMapView` below, which is presentational/query-free — the
 *  mission detail page feeds it a MISSION-scoped projection (no run) through its own query. */
export function TerrainMap({
  runId,
  active = false,
  selectedEventId = null,
  attributionOff = false,
  expandable = false,
  onHoverEvents,
  onReturnToLive,
}: {
  runId: string;
  active?: boolean;
  selectedEventId?: string | null;
  onReturnToLive?: () => void;
  attributionOff?: boolean;
  expandable?: boolean;
  onHoverEvents?: (eventIds: string[]) => void;
}) {
  const { terrain, isError } = useRunTerrain(runId, active);
  return (
    <TerrainMapView
      terrain={terrain}
      isError={isError}
      resetKey={runId}
      active={active}
      selectedEventId={selectedEventId}
      attributionOff={attributionOff}
      expandable={expandable}
      onHoverEvents={onHoverEvents}
      onReturnToLive={onReturnToLive}
    />
  );
}

export function TerrainMapView({
  terrain,
  isError = false,
  resetKey = "",
  active = false,
  selectedEventId = null,
  attributionOff = false,
  expandable = false,
  caption,
  onHoverEvents,
  onReturnToLive,
}: {
  /** The resolved v2 terrain to draw (null while loading/absent). Run-scoped (with updates)
   *  or mission-scoped (base graph only) — the fold below is a no-op on an empty updates array. */
  terrain: ResolvedTerrainV2 | null;
  isError?: boolean;
  /** Identity of the drawn subject (run id / mission id) — a new key restarts auto-fit
   *  following from scratch, exactly as a new run used to. */
  resetKey?: string;
  active?: boolean;
  selectedEventId?: string | null;
  /** Return-to-live escape from time-travel: when a span is selected the map is frozen at that
   *  fold, so a control asks RunLive (which owns the selection) to clear it. Without this the only
   *  way back to the live/latest view was a page refresh. */
  onReturnToLive?: () => void;
  /** No-model degradation notice: true when no terrain attribution model is
   *  configured — RunLive derives this from `useConfig().terrain.configured` so TerrainMap
   *  stays presentational/query-free. */
  attributionOff?: boolean;
  /** Offer the fullscreen toggle (bottom-right cluster): expanded, the card re-homes into a
   *  fixed full-viewport overlay — Escape, the backdrop, or the toggle collapse it. */
  expandable?: boolean;
  /** Footer caption override — the default speaks run-language (amber ring / trace events);
   *  the mission preview passes its own. */
  caption?: string;
  /** Reverse hover link: hovering a node reports the event ids of the updates
   *  that targeted it, so RunLive can highlight them in the Trace. */
  onHoverEvents?: (eventIds: string[]) => void;
}) {

  // TIME-TRAVEL wiring: `k` is the array index to fold to. Selection is prop-driven (owned by
  // RunLive) — it derives from `selectedEventId` every render, so a poll that advances "latest"
  // never yanks a selected rewind forward. Never reset the selection from an effect here.
  const updates = terrain?.updates ?? [];
  const latestK = updates.length - 1;
  const k = selectedEventId ? (foldIndexForEvent(updates, selectedEventId) ?? latestK) : latestK;
  const pulseIds = selectedEventId
    ? pulseIdsForIndex(updates, k)
    : latestK >= 0
      ? pulseIdsForIndex(updates, latestK)
      : null;

  // The SINGLE node to pulse on the LIVE view: the drawn service the agent's LAST ATTRIBUTED
  // (model-attributed, `event_id != null`) node action landed on, up to the current fold `k`. This
  // replaces the old "halo on every discovered node" — only where the agent last acted pulses.
  // During time-travel (a span is selected) the amber delta ring carries "what changed" instead, so
  // the hot halo is suppressed to keep a single, unambiguous highlight per mode.
  const hotDrawnId = useMemo(() => {
    if (selectedEventId) return null;
    for (let i = Math.min(latestK, updates.length - 1); i >= 0; i--) {
      const u = updates[i];
      if (u.target_kind === "node" && u.event_id) return drawnParentId(u.target_id);
    }
    return null;
  }, [updates, latestK, selectedEventId]);

  const folded = useMemo(
    () => (terrain ? foldTerrain(terrain, k, pulseIds) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pulseIds is a freshly-built Set every
    // render (cheap to recompute); depending on it directly would never memoize, so key off its
    // stable inputs instead.
    [terrain, k, selectedEventId],
  );
  const layout = useMemo(() => (folded ? layoutTerrainV2(folded) : null), [folded]);

  // The mission plane is gated grey until the agent emits OTel (collector discovered). Node fill
  // also needs each node's group kind (infra / agent / segment) — precompute a lookup once.
  const otel = useMemo(() => (folded ? otelActive(folded.nodes) : false), [folded]);
  const groupKind = useMemo(() => {
    const m = new Map<string, string>();
    for (const g of folded?.groups ?? []) m.set(g.group.id, g.group.kind);
    return m;
  }, [folded]);
  // A mission group turns green only once ALL its member nodes are enumerated (completed);
  // discovered-but-incomplete stays blue. Precompute per-group once.
  const groupEnumerated = useMemo(() => {
    const byGroup = new Map<string, boolean>();
    const members = new Map<string, number>();
    const done = new Map<string, number>();
    for (const n of folded?.nodes ?? []) {
      members.set(n.node.group, (members.get(n.node.group) ?? 0) + 1);
      if (n.state === "completed") done.set(n.node.group, (done.get(n.node.group) ?? 0) + 1);
    }
    for (const [gid, count] of members) byGroup.set(gid, count > 0 && done.get(gid) === count);
    return byGroup;
  }, [folded]);

  // "Probing" (yellow) edges: not just the single delta edge, but the WHOLE path from the probed
  // node back to the agent (agent→web→internal all light while internal is being probed). Seed the
  // walk from the pulsed nodes + the dst of any pulsed edge, then trace inbound edges to the agent.
  const probingEdges = useMemo(() => {
    if (!folded) return new Set<string>();
    const agentIds = new Set(
      folded.nodes.filter((n) => n.node.type === "agent").map((n) => n.node.id),
    );
    const baseEdges = folded.edges.map((e) => e.edge);
    const seed = new Set<string>(folded.pulse.nodes);
    for (const e of baseEdges) if (folded.pulse.edges.has(e.id)) seed.add(e.dst);
    const path = probingPathEdgeIds(seed, baseEdges, agentIds);
    for (const id of folded.pulse.edges) path.add(id); // keep any directly-pulsed edge lit too
    return path;
  }, [folded]);

  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  // Fullscreen toggle (expandable only): the SAME component instance re-homes into a fixed
  // overlay — no second map, no remount — so pan/zoom and the shared terrain query carry
  // over, and the ResizeObserver's auto-fit refits the graph to the larger viewport.
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);
  // Summary clamp (issue #22): collapsed by default; `summaryOverflows` gates the toggle so
  // a summary that fits in two lines shows no dead "Show more" control. Measured (scroll vs
  // client height) rather than guessed from character count — wrap width varies pane to
  // pane. Re-measured on window resize (jsdom's ResizeObserver-free path too) and via a
  // ResizeObserver on the element itself; `expanded` in the deps re-binds to the element the
  // fullscreen portal toggle re-creates, `summaryExpanded` re-measures after each toggle.
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  const [summaryOverflows, setSummaryOverflows] = useState(false);
  const summaryRef = useRef<HTMLParagraphElement | null>(null);
  useEffect(() => {
    const el = summaryRef.current;
    if (!el) return;
    const measure = () => setSummaryOverflows(el.scrollHeight > el.clientHeight + 1);
    measure();
    window.addEventListener("resize", measure);
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(el);
    return () => {
      window.removeEventListener("resize", measure);
      ro?.disconnect();
    };
  }, [terrain?.summary, summaryExpanded, expanded]);
  // Hover state for the node popover (description + collapsed routes, Change 2). An edge's
  // route-used note (Change 3) is rendered persistently at the edge midpoint instead — no hover
  // state needed for it.
  const [hoverNodeId, setHoverNodeId] = useState<string | null>(null);
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  // AUTO-FIT FOLLOW: the map keeps the whole graph in view — on first render, and again whenever the
  // pane resizes (split-divider drag, window, tab switch) or the graph grows (live nodes appear) —
  // UNTIL the user takes manual control (pan/zoom). `userControlled` latches that hand-off so a poll
  // can never yank a deliberately-panned view back; the Fit button clears it to resume following.
  const userControlled = useRef(false);
  const fittedDims = useRef<{ w: number; h: number } | null>(null);
  // Latest layout behind a ref so `fitToView` is a STABLE callback — the ResizeObserver below then
  // subscribes once instead of re-subscribing on every 1.5s poll (each poll makes a new `layout`).
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  // Flips false→true once the graph resolves (and stays true) — gates the ResizeObserver so it
  // attaches only after the viewport it observes exists, without churning on every poll.
  const hasLayout = layout !== null;

  // Fit the whole graph into the viewport. The top-right holds the legend overlay, so the fit
  // RESERVES that column and centres the graph in the remaining area — a fitted node can't land
  // under the legend (which was clipping the OTel-collector node). A small pad keeps the graph off
  // the gutter too.
  const fitToView = useCallback(() => {
    const el = containerRef.current;
    const lo = layoutRef.current;
    if (!el || !lo) return;
    const cw = el.clientWidth;
    const ch = el.clientHeight;
    if (!cw || !ch) return;
    const PAD = 10;
    const LEGEND_RESERVE = 132; // ≈ the legend overlay's width; kept clear on the right
    const availW = Math.max(80, cw - LEGEND_RESERVE - PAD);
    const availH = Math.max(80, ch - PAD * 2);
    const fit = clampZoom(Math.min(availW / lo.width, availH / lo.height));
    setZoom(fit);
    setPan({
      x: Math.max(PAD, PAD + (availW - lo.width * fit) / 2),
      y: Math.max(PAD, (ch - lo.height * fit) / 2),
    });
  }, []);
  // Zoom by `factor`, keeping `anchor` (container-local px; defaults to the VIEWPORT CENTER)
  // fixed — so content doesn't fly toward the 0,0 transform origin. The +/- buttons zoom about
  // the center; the wheel passes the cursor position so zoom is cursor-anchored.
  const zoomBy = useCallback(
    (factor: number, anchor?: { x: number; y: number }) => {
      userControlled.current = true; // a manual zoom (wheel or ± buttons) opts out of auto-fit
      const el = containerRef.current;
      const z1 = clampZoom(zoom * factor);
      if (el && el.clientWidth && el.clientHeight) {
        const cx = anchor?.x ?? el.clientWidth / 2;
        const cy = anchor?.y ?? el.clientHeight / 2;
        setPan({
          x: cx - ((cx - pan.x) / zoom) * z1,
          y: cy - ((cy - pan.y) / zoom) * z1,
        });
      }
      setZoom(z1);
    },
    [zoom, pan],
  );

  // WHEEL-ZOOM GATING (scroll-hijack fix): a plain wheel over the map must scroll the PAGE unless
  // the user opted in — Ctrl/⌘+wheel always zooms, and engaging the map (pointerdown or focus)
  // activates plain-wheel zoom until blur/Escape. Ref-not-state: nothing renders from activation,
  // and the native handler below needs the current value without re-subscribing.
  const wheelActiveRef = useRef(false);
  const zoomByRef = useRef(zoomBy);
  useEffect(() => {
    zoomByRef.current = zoomBy;
  });
  const [wheelTip, setWheelTip] = useState(false);
  const tipTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showWheelTip = useCallback(() => {
    setWheelTip(true);
    if (tipTimer.current) clearTimeout(tipTimer.current);
    tipTimer.current = setTimeout(() => setWheelTip(false), 1500);
  }, []);
  useEffect(
    () => () => {
      if (tipTimer.current) clearTimeout(tipTimer.current);
    },
    [],
  );
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    // NATIVE non-passive listener — React's root-delegated wheel listener is passive, so a JSX
    // onWheel could never preventDefault (the old handler zoomed AND the page still scrolled).
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey || wheelActiveRef.current) {
        e.preventDefault(); // zooming: the map consumes the wheel, the page must not scroll
        const rect = el.getBoundingClientRect();
        zoomByRef.current(e.deltaY < 0 ? 1.1 : 0.9, {
          x: e.clientX - rect.left,
          y: e.clientY - rect.top,
        });
      } else {
        showWheelTip(); // inactive: let the page scroll, teach the affordance
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `layout` re-runs the attach when the
    // container (re)mounts: it only renders once layout exists, so the first mount is never missed.
    // `expanded` is load-bearing too: the fullscreen portal toggle re-creates the viewport
    // ELEMENT, so without it this native listener stays bound to the detached old node and
    // wheel zoom dies after a toggle (for a terminal run `layout` never changes to re-run it).
  }, [layout, showWheelTip, expanded]);
  useEffect(() => {
    // A new subject (run / mission) starts following again from scratch — auto-fit AND the
    // summary clamp (an expanded summary is a per-subject reading choice, not a setting).
    fittedDims.current = null;
    userControlled.current = false;
    setSummaryExpanded(false);
  }, [resetKey]);
  useEffect(() => {
    // Fit on the FIRST render, and re-fit whenever the graph's drawn size changes (live nodes
    // appear) — unless the user has taken manual control. Keyed on the actual dimensions so a poll
    // that only recolors nodes (same size) doesn't refit; growth does.
    const el = containerRef.current;
    if (!layout || !el?.clientWidth) return;
    const prev = fittedDims.current;
    if (prev && prev.w === layout.width && prev.h === layout.height) return;
    fittedDims.current = { w: layout.width, h: layout.height };
    if (prev === null || !userControlled.current) fitToView();
  }, [layout, fitToView]);
  useEffect(() => {
    // Keep the graph fit through CONTAINER resizes too — the split-divider drag, a window resize,
    // switching into the Terrain tab. Same hand-off rule: once the user pans/zooms we stop. Guarded
    // for jsdom (no ResizeObserver) so unit tests fall through to manual zoom.
    //
    // `hasLayout` in the deps is load-bearing: on the FIRST mount the terrain is still loading, so
    // the viewport (and `containerRef.current`) doesn't exist yet and this would early-return; the
    // viewport only appears once `layout` resolves, and `hasLayout` flipping false→true re-runs the
    // effect exactly then so the observer actually attaches (it stays true after, so no churn).
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (!userControlled.current) fitToView();
    });
    ro.observe(el);
    return () => ro.disconnect();
    // `expanded` re-attaches the observer to the element the fullscreen portal toggle just
    // re-created — observe() fires once on attach, which is also what refits the graph to the
    // new viewport size on both expand and collapse (unless the user has taken control).
  }, [fitToView, hasLayout, expanded]);

  if (isError)
    return (
      <div className="rounded-md border border-border bg-card p-4 text-body text-err">
        Couldn’t load the terrain.
      </div>
    );
  if (!terrain || !layout || (terrain.nodes ?? []).length === 0)
    return (
      <div className="rounded-md border border-border bg-card p-4 text-body text-text-secondary">
        No terrain yet.
      </div>
    );

  // Reverse hover link: the event ids of updates that targeted this node.
  const eventIdsForNode = (nodeId: string): string[] =>
    updates
      .filter((u) => u.target_kind === "node" && u.target_id === nodeId && u.event_id)
      .map((u) => u.event_id as string);

  // Route-used annotation (Change 3): the note of the MOST-RECENT update targeting this edge, up
  // to the current fold boundary `k` — nothing shown when no such update carries a note.
  const noteForEdge = (edgeId: string): string | null => {
    const upTo = Math.min(k, updates.length - 1);
    for (let i = upTo; i >= 0; i--) {
      const u = updates[i];
      if (u.target_kind === "edge" && u.target_id === edgeId && u.note) return u.note;
    }
    return null;
  };

  // No-model degradation notice: only meaningful once there's a mission (segment) group.
  const hasMissionGroup = (terrain.groups ?? []).some((g) => g.kind === "segment");

  const body = (
    // Collapsed, the wrapper is layout-transparent (`contents`) so the card sits in the page
    // exactly as before; expanded, it becomes the fixed backdrop the card fills — rendered
    // through a body-level portal (see the return below), because `fixed` positions against
    // the nearest TRANSFORMED ancestor, not the viewport: the narrow layout's TabsContent
    // animates `transform` (animate-fade-up), which trapped the overlay inside the Terrain
    // pane. The component instance (zoom/pan/queries) survives the toggle; only the DOM
    // subtree re-homes, and the element-bound effects above re-attach off `expanded`.
    <div
      className={expanded ? "fixed inset-0 z-50 flex bg-black/60 p-3 sm:p-6" : "contents"}
      role={expanded ? "dialog" : undefined}
      aria-modal={expanded || undefined}
      aria-label={expanded ? "Terrain map — fullscreen" : undefined}
      onClick={expanded ? () => setExpanded(false) : undefined}
    >
      <div
        // `relative` anchors the expanded-summary overlay below, which spans the card from
        // its top edge OVER the map — never through the layout.
        className="relative flex h-full w-full flex-col rounded-md border border-border bg-card"
        onClick={expanded ? (e) => e.stopPropagation() : undefined}
      >
      {terrain.summary && (
        // Clamped to TWO lines behind an EXPLICIT toggle (issue #22). This block has now
        // swung THREE times, so the history matters: the original map truncated the summary
        // to one line and revealed the rest only on hover — a sentence you have to hover to
        // finish is a sentence nobody reads — so a later pass made it wrap in full. That
        // assumed summaries "run to two or three lines"; shipped missions run to paragraph
        // length, and every wrapped line came straight out of the map viewport below.
        //
        // The clamp keeps the no-hover principle (the toggle is a persistent CLICK target
        // announcing its state via aria-expanded), and the strip is LAYOUT-FROZEN: it stays
        // clamped even while "expanded" — the full text renders as the overlay below, drawn
        // OVER the map. The first cut expanded in-flow, which pushed the viewport down and
        // shoved the zoom cluster out of a fixed-height card (the mission page's 480px box);
        // nothing in this card may move because prose was opened. line-clamp hides visually
        // only, so this in-flow copy keeps the full text for assistive tech / find-in-page.
        <div className="shrink-0 border-b border-border px-4 py-2">
          <p
            ref={summaryRef}
            data-testid="terrain-summary"
            className="line-clamp-2 text-caption text-text-secondary"
          >
            {terrain.summary}
          </p>
          {summaryOverflows && !summaryExpanded && (
            <button
              type="button"
              aria-expanded={false}
              onClick={() => setSummaryExpanded(true)}
              className="mt-1 text-caption text-text-tertiary underline-offset-2 hover:text-foreground hover:underline"
            >
              Show more
            </button>
          )}
        </div>
      )}
      {terrain.summary && summaryExpanded && (
        // The expanded summary, as a LAYOUT-INERT overlay over the top of the map. Same
        // padding as the strip, so its first two lines land exactly over their clamped
        // copies — it reads as the strip growing over the graph, not a popover appearing.
        // The text is aria-hidden (the in-flow strip already carries the full accessible
        // copy — never announce the same sentence twice); the Show less control stays
        // outside the hidden node so it remains focusable. z-30 clears the map region's
        // pills (z-20); max-h + scroll is the backstop for a summary taller than the card.
        <div
          data-testid="terrain-summary-overlay"
          className="absolute inset-x-0 top-0 z-30 max-h-full overflow-y-auto rounded-t-md border-b border-border bg-card px-4 py-2 shadow-lg"
        >
          <p aria-hidden="true" className="text-caption text-text-secondary">
            {terrain.summary}
          </p>
          <button
            type="button"
            aria-expanded={true}
            onClick={() => setSummaryExpanded(false)}
            className="mt-1 text-caption text-text-tertiary underline-offset-2 hover:text-foreground hover:underline"
          >
            Show less
          </button>
        </div>
      )}
      <div className="relative min-h-[360px] flex-1">
        <div
          ref={containerRef}
          data-testid="terrain-viewport"
          // Inset a few px from the card's rounded border and clip HERE, so panned/zoomed content
          // stays inside a clean gutter and never butts up against (or paints over) the outer
          // border — the fit measures this inset box, so a fitted graph gets the gutter too.
          className="absolute inset-[3px] overflow-hidden rounded-[4px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          // focusable so focus/blur/Escape can gate plain-wheel zoom (the wheel listener itself is
          // native + non-passive, attached in the effect above)
          tabIndex={0}
        onFocus={() => {
          wheelActiveRef.current = true;
        }}
        onBlur={() => {
          wheelActiveRef.current = false;
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") wheelActiveRef.current = false;
        }}
        onPointerDown={(e) => {
          wheelActiveRef.current = true; // engaging the map opts into plain-wheel zoom
          drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
          setDragging(true);
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!drag.current) return;
          userControlled.current = true; // a manual pan opts out of auto-fit
          setPan({
            x: drag.current.px + (e.clientX - drag.current.x),
            y: drag.current.py + (e.clientY - drag.current.y),
          });
        }}
        onPointerUp={() => {
          drag.current = null;
          setDragging(false);
        }}
        style={{ cursor: dragging ? "grabbing" : "grab" }}
      >
        <div
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: "0 0",
            transition: dragging ? "none" : "transform 0.15s ease-out",
          }}
        >
          <svg
            width={layout.width}
            height={layout.height}
            role="img"
            aria-label="terrain map"
            data-testid="terrain-svg"
          >
            {layout.groups.map((lg) => (
              <GroupBox
                key={lg.fgroup.group.id}
                lg={lg}
                pulsed={folded!.pulse.groups.has(lg.fgroup.group.id)}
                otel={otel}
                enumerated={groupEnumerated.get(lg.fgroup.group.id) ?? false}
              />
            ))}
            {layout.edges.map((le) => (
              <EdgeLine
                key={le.fedge.edge.id}
                le={le}
                pulsed={probingEdges.has(le.fedge.edge.id)}
                note={noteForEdge(le.fedge.edge.id)}
              />
            ))}
            {layout.nodes.map((ln) => (
              <NodeMark
                key={ln.fnode.node.id}
                ln={ln}
                // amber delta ring only during time-travel; the live "where the agent is" cue is the
                // hot halo below — so exactly one highlight shows per mode
                pulsed={!!selectedEventId && folded!.pulse.nodes.has(ln.fnode.node.id)}
                hot={ln.fnode.node.id === hotDrawnId}
                color={nodeColor(ln.fnode, groupKind.get(ln.fnode.node.group), otel)}
                onEnter={() => {
                  onHoverEvents?.(eventIdsForNode(ln.fnode.node.id));
                  setHoverNodeId(ln.fnode.node.id);
                }}
                onLeave={() => {
                  onHoverEvents?.([]);
                  setHoverNodeId((h) => (h === ln.fnode.node.id ? null : h));
                }}
              />
            ))}
            {/* persistent route-used note on every acted-on edge — the action lives on the line
                it acted on, not gated behind hover (drawn on top of everything else) */}
            {layout.edges.map((le) => {
              const noteText = noteForEdge(le.fedge.edge.id);
              return noteText ? (
                <EdgeNoteTooltip key={`note-${le.fedge.edge.id}`} le={le} note={noteText} />
              ) : null;
            })}
            {/* hover popover, drawn on top of everything else */}
            {hoverNodeId &&
              (() => {
                const ln = layout.nodes.find((n) => n.fnode.node.id === hoverNodeId);
                return ln ? <NodeTooltip ln={ln} /> : null;
              })()}
          </svg>
          </div>
        </div>

        <Legend />

        {/* Return-to-live escape from time-travel. Shown whenever a span is selected (the map is
            frozen at that fold): a prominent top-centre pill — same idiom as the Trace's pill — that
            clears the selection so the map rejoins the live/latest view. stopPropagation on
            pointerdown so the pannable container beneath can't capture the click. */}
        {selectedEventId && onReturnToLive && (
          <div
            className="absolute left-1/2 top-2 z-20 -translate-x-1/2"
            onPointerDown={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={onReturnToLive}
              className="flex items-center gap-1.5 rounded-full border border-primary/40 bg-card/90 px-3 py-1 text-caption text-primary shadow-lg backdrop-blur hover:border-primary"
            >
              <Radio className="size-3" />
              {active ? "Return to live" : "Back to latest"}
            </button>
          </div>
        )}

        {/* transient teach chip — shown when a plain wheel scrolled past an inactive map */}
        {wheelTip && (
          <div
            data-testid="wheel-tip"
            className="pointer-events-none absolute left-1/2 top-2 -translate-x-1/2 whitespace-nowrap rounded-md border border-border bg-card/90 px-2.5 py-1.5 text-caption text-text-secondary backdrop-blur"
          >
            Ctrl/⌘ + scroll to zoom — or click the map
          </div>
        )}

        {/* stopPropagation on pointerdown: the container captures the pointer for panning
            (setPointerCapture), which otherwise swallows these buttons' click events. */}
        <div
          className="absolute bottom-2 right-2 flex items-center gap-1 text-text-secondary"
          onPointerDown={(e) => e.stopPropagation()}
        >
          {expandable && (
            <button
              type="button"
              aria-label={expanded ? "exit fullscreen" : "fullscreen"}
              title={expanded ? "Exit fullscreen (Esc)" : "Fullscreen"}
              onClick={() => setExpanded((v) => !v)}
              className="flex items-center self-stretch rounded border border-border bg-card px-2 hover:text-foreground"
            >
              {expanded ? (
                <Minimize2 className="size-3" aria-hidden />
              ) : (
                <Maximize2 className="size-3" aria-hidden />
              )}
            </button>
          )}
          <button
            type="button"
            aria-label="zoom out"
            onClick={() => zoomBy(0.9)}
            className="rounded border border-border bg-card px-2 py-0.5 text-dense hover:text-foreground"
          >
            −
          </button>
          <button
            type="button"
            aria-label="fit to view"
            onClick={() => {
              userControlled.current = false; // resume auto-fit following
              fitToView();
            }}
            className="rounded border border-border bg-card px-2 py-0.5 text-dense hover:text-foreground"
          >
            Fit
          </button>
          <button
            type="button"
            aria-label="zoom in"
            onClick={() => zoomBy(1.1)}
            className="rounded border border-border bg-card px-2 py-0.5 text-dense hover:text-foreground"
          >
            +
          </button>
        </div>
      </div>

      {/* Only the facts the legend doesn't already carry: the amber (just-changed) ring and the
          hover interaction. The colour→state mapping lives in the legend, so it's dropped here to
          keep this caption short enough to wrap cleanly inside the card. */}
      <p className="shrink-0 border-t border-border px-4 py-2 text-caption text-text-tertiary">
        {caption ?? "Amber ring = what just changed · hover a node to highlight its trace events."}
      </p>
      {attributionOff && hasMissionGroup && (
        <p className="shrink-0 border-t border-border px-4 py-2 text-caption italic text-text-tertiary">
          target attribution off — set a model in Settings
        </p>
      )}
      </div>
    </div>
  );
  // Fullscreen must escape every ancestor: a transformed/filtered/overflow-clipping parent
  // (the tabs' entrance animation, a pane's overflow-hidden) becomes `fixed`'s containing
  // block and pins the overlay inside the pane. Portaling to <body> is the standard escape.
  // `expanded` only flips on a user click, so this never runs during SSR/prerender.
  return expanded ? createPortal(body, document.body) : body;
}
