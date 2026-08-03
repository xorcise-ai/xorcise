"use client";

import { cn } from "@/components/ui/cn";
import type { AgentEvent } from "@/lib/api/types";
import type { InfraRow } from "./terrain-fold";
import {
  COLOR_FAMILIES,
  KIND_META,
  displayLabel,
  eventColor,
  formatDuration,
  formatEventTime,
} from "@/features/replay/kind-meta";

/** Minimum width (%) for a bar that carries a real duration, so a short span stays visible. */
const MIN_WIDTH_PCT = 1.2;

const parseMs = (ts: string | null | undefined): number => (ts ? Date.parse(ts) || 0 : 0);

/** A "nice" tick interval yielding ~4-6 ticks across the run span. */
export function niceTickStep(totalMs: number): number {
  const target = totalMs / 5;
  const steps = [
    100, 250, 500, 1_000, 2_000, 5_000, 10_000, 15_000, 30_000, 60_000, 120_000, 300_000,
    600_000, 900_000, 1_800_000, 3_600_000,
  ];
  for (const s of steps) if (s >= target) return s;
  return Math.ceil(target / 3_600_000) * 3_600_000;
}

/** An elapsed-time tick label — "0:30", "1:05"; sub-second steps keep one decimal ("0:00.5"). */
function tickLabel(ms: number, stepMs: number): string {
  const totalSec = ms / 1000;
  const m = Math.floor(totalSec / 60);
  const s = totalSec - m * 60;
  const sStr =
    stepMs < 1000 ? s.toFixed(1).padStart(4, "0") : String(Math.round(s)).padStart(2, "0");
  return `${m}:${sStr}`;
}

/** One thing plotted on the line — an agent event OR a deterministic infra activity. */
interface Mark {
  id: string;
  start: number;
  end: number;
  /** `bg-*` colour token (event family colour); infra rows render as a neutral milestone instead. */
  dotClass: string;
  title: string;
  durationMs: number | null;
  infra: boolean;
  /** true = reasoning (COT / thinking / user prompt) → the tick points UP from the rail; false =
   *  action (shell / tool / file …) → it points DOWN. Splitting the two sides means a COT message
   *  and the shell command a millisecond later don't land on the same pixel and cover each other. */
  up: boolean;
}

/**
 * A run's activity as a SINGLE-LINE timeline: one amber rail across the run span with a marker per
 * event placed at its time — an event you hover to identify (what it was + when) and click to select
 * (shared `selectedEventId` → trace scroll + terrain highlight). Mirrors the v1 skill-version rail:
 * a coloured line with dash-ticks on it.
 *
 * Two kinds of mark share the one line:
 *   • agent events — a thin coloured dash-tick per event (colour = family: COT / Shell / Tool / …),
 *     a short bar when the event carries a real duration. Markers are POINTS, not gap-filled bars,
 *     so simultaneous events sit side-by-side instead of one covering the other.
 *   • infra activities (join the tailnet, fetch the brief, submit an artifact, mark done…) — the
 *     deterministic platform milestones that show in the Trace. They were absent from the timeline
 *     before; now they appear as neutral hollow-diamond milestones so nothing in the trace is missing.
 */
export function TimelineWaterfall({
  events,
  infraRows = [],
  selectedEventId = null,
  onSelectEvent,
}: {
  events: AgentEvent[];
  /** Deterministic infra activities (from the terrain plane) to plot as milestones alongside the
   *  agent events, so the timeline carries every row the Trace does. */
  infraRows?: InfraRow[];
  /** Shared Timeline↔Trace↔Terrain selection: the currently selected event/activity id. */
  selectedEventId?: string | null;
  onSelectEvent?: (id: string) => void;
}) {
  // Drop the KIND_META "debug" group (metric, unknown): one grey `metric` marker per LLM call
  // dominated the timeline (and its legend) and crowded out the meaningful events. The Trace
  // replay already hides this group by default; the timeline matches.
  const shown = events.filter((e) => (KIND_META[e.kind] ?? KIND_META.unknown).group !== "debug");
  // Infra activities need a timestamp to place on the time scale; an anchorless one (rare) is skipped.
  const infra = infraRows.filter((r) => r.ts);

  if (shown.length === 0 && infra.length === 0)
    return <p className="text-body text-text-secondary">No timeline yet.</p>;

  // Agent events first (in arrival order — keeps the marker order input-driven), then infra. Position
  // is by timestamp regardless of order, so order only affects DOM/paint sequence.
  const eventMarks: Mark[] = shown.map((e) => {
    const s = parseMs(e.ts);
    const hasDur = e.duration_ms != null && e.duration_ms > 0;
    return {
      id: e.id,
      start: s,
      end: hasDur ? s + e.duration_ms! : s,
      dotClass: eventColor(e.kind, e.role).dotClass,
      title: displayLabel(e).title,
      durationMs: e.duration_ms ?? null,
      infra: false,
      up: (KIND_META[e.kind] ?? KIND_META.unknown).group === "conversation",
    };
  });
  const infraMarks: Mark[] = infra.map((r) => {
    const s = parseMs(r.ts);
    return { id: r.id, start: s, end: s, dotClass: "bg-text-tertiary", title: r.label, durationMs: null, infra: true, up: false };
  });
  const marks = [...eventMarks, ...infraMarks];

  const t0 = Math.min(...marks.map((m) => m.start));
  const tMax = Math.max(...marks.map((m) => m.end));
  const total = tMax - t0;
  // All timestamps equal (and no durations): fall back to an even sequence so the marks stay
  // distinguishable instead of stacking at left 0.
  const degenerate = total < 1;

  const leftPctOf = (m: Mark, i: number): number =>
    degenerate ? (i / marks.length) * 100 : ((m.start - t0) / total) * 100;

  // The rail — the amber "line" the marks sit on — spans from the first mark to the last.
  const railEndPct = degenerate
    ? 100
    : Math.max(0, ...marks.map((m) => ((m.end - t0) / total) * 100));

  // Legend: the colour families actually present among the agent events, deduped by colour (many
  // kinds share a colour). Role-aware. Infra gets its own manual entry when present.
  const presentDotClasses = new Set(shown.map((e) => eventColor(e.kind, e.role).dotClass));
  const legendFamilies = COLOR_FAMILIES.filter((f) => presentDotClasses.has(f.dotClass));

  return (
    <div className="space-y-2">
      {/* Legend first, so the track + axis sit flush above the terrain/trace work area below. */}
      <div
        data-testid="timeline-legend"
        className="flex flex-wrap items-center gap-x-3 gap-y-1"
      >
        {legendFamilies.map((f) => (
          <span key={f.dotClass} className="inline-flex items-center gap-1.5">
            <span
              data-testid="legend-swatch"
              className={cn("size-2 shrink-0 rounded-sm", f.dotClass)}
            />
            <span className="text-caption text-text-tertiary">{f.label}</span>
          </span>
        ))}
        {infra.length > 0 && (
          <span className="inline-flex items-center gap-1.5">
            <span
              data-testid="legend-swatch"
              className="size-2 shrink-0 rotate-45 rounded-[1px] border border-text-tertiary"
            />
            <span className="text-caption text-text-tertiary">Infra</span>
          </span>
        )}
      </div>

      {/* The single timeline track: an amber run-span rail with a marker per event/activity. The
          marks + rail live in an inset layer (inset-x-2) so a mark at the very start or very end
          isn't half-clipped by the track's rounded, overflow-hidden edge. */}
      <div
        data-testid="timeline-lane"
        className="relative h-8 overflow-hidden rounded-md border border-border bg-card"
      >
        <div className="absolute inset-x-2 inset-y-0">
        {/* the run-span rail — the "line" the dash-ticks sit on */}
        <div
          className="pointer-events-none absolute top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-primary/25"
          style={{ left: "0%", width: `${railEndPct}%` }}
        />

        {marks.map((m, i) => {
          const leftPct = leftPctOf(m, i);
          const selected = m.id === selectedEventId;
          const tip = `${m.title} · ${formatEventTime(new Date(m.start).toISOString())}${
            m.durationMs ? ` · ${formatDuration(m.durationMs)}` : ""
          }`;

          // Infra activity → a neutral hollow diamond milestone (distinct from the coloured agent
          // dash-ticks), on top of the rail.
          if (m.infra) {
            return (
              <button
                key={m.id}
                type="button"
                data-testid="timeline-infra"
                data-selected={selected || undefined}
                title={tip}
                aria-label={tip}
                onClick={() => onSelectEvent?.(m.id)}
                className={cn(
                  "absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rotate-45 cursor-pointer rounded-[1px] border bg-card transition hover:scale-125 focus-visible:outline-none",
                  selected
                    ? "z-10 border-primary ring-2 ring-primary"
                    : "border-text-tertiary",
                )}
                style={{ left: `${leftPct}%` }}
              />
            );
          }

          // A duration-bearing event draws as a short bar of that width; an instantaneous one is a
          // thin dash-tick — never gap-filled, so simultaneous events don't cover each other.
          if (m.durationMs && m.durationMs > 0 && !degenerate) {
            let widthPct = Math.max((m.durationMs / total) * 100, MIN_WIDTH_PCT);
            widthPct = Math.min(widthPct, Math.max(0, 100 - leftPct));
            return (
              <button
                key={m.id}
                type="button"
                data-testid="timeline-bar"
                data-selected={selected || undefined}
                title={tip}
                aria-label={tip}
                onClick={() => onSelectEvent?.(m.id)}
                className={cn(
                  "absolute top-1/2 h-3 -translate-y-1/2 cursor-pointer rounded-sm opacity-90 transition hover:opacity-100 focus-visible:outline-none",
                  selected ? "z-10 opacity-100 ring-2 ring-primary" : "ring-1 ring-card/40",
                  m.dotClass,
                )}
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
              />
            );
          }

          // Reasoning (COT / thinking / user) ticks are anchored at the rail and point UP; action
          // ticks point DOWN — so a COT message and the shell command a millisecond later sit on
          // opposite sides of the line and neither hides the other (all 16 COT ticks overlapped a
          // shell tick before). Grows on hover/selection for a bigger target.
          return (
            <button
              key={m.id}
              type="button"
              data-testid="timeline-bar"
              data-selected={selected || undefined}
              title={tip}
              aria-label={tip}
              onClick={() => onSelectEvent?.(m.id)}
              className={cn(
                "absolute w-[3px] -translate-x-1/2 cursor-pointer rounded-full opacity-90 transition-all hover:h-4 hover:opacity-100 focus-visible:outline-none",
                m.up ? "bottom-1/2" : "top-1/2",
                selected ? "z-10 h-4 opacity-100 ring-2 ring-primary" : "h-3.5",
                m.dotClass,
              )}
              style={{ left: `${leftPct}%` }}
            />
          );
        })}
        </div>
      </div>

      {/* Time-axis ruler: elapsed-from-start ticks on the same left% scale as the marks.
          Skipped when the span is degenerate (no meaningful time scale to rule). */}
      {!degenerate &&
        (() => {
          const step = niceTickStep(total);
          const ticks: number[] = [];
          for (let t = 0; t <= total; t += step) ticks.push(t);
          return (
            <div data-testid="timeline-axis" className="relative mx-2 h-4">
              {ticks.map((t) => (
                <span
                  key={t}
                  data-testid="timeline-tick"
                  className="absolute top-0 h-full"
                  style={{ left: `${(t / total) * 100}%` }}
                >
                  <span className="absolute top-0 h-1.5 w-px bg-border" />
                  <span className="absolute top-1.5 -translate-x-1/2 whitespace-nowrap font-mono text-caption tabular-nums text-text-tertiary">
                    {tickLabel(t, step)}
                  </span>
                </span>
              ))}
            </div>
          );
        })()}
    </div>
  );
}
