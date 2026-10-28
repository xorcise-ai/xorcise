"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import { cn } from "@/components/ui/cn";
import type { RunScore } from "@/lib/api/types";
import { hatch, runGlyph, type RunKind } from "./run-glyph";
import { RunInspector } from "./run-inspector";
import { useFitCount } from "./use-fit-count";

// Column sizing. 8px is the narrowest a column still reads as its own run with both halves
// distinguishable; 48 caps a very wide card, past which single runs stop being worth telling
// apart and the median says more. Without a measurement (first paint, jsdom) 24 fit a ~280px card.
const MIN_COLUMN = 8;
const GAP = 3;
const MAX_COLUMNS = 48;
const FALLBACK_COLUMNS = 24;

/**
 * An agent's latest runs, one column each, oldest to newest. A column's height is the run's
 * overall score: the lower part is what the deterministic checks earned, the upper part what the
 * judge earned. Each kind of run is told apart by the column itself, not by a mark beside it:
 *
 * - timed out: a red cap. Red means only this, the agent did not finish. It is in the median;
 * - partial judge: hatched from its lower bound to its upper;
 * - no judge: the judge half a dashed outline, unmeasured rather than zero;
 * - stopped by an operator: grey, since its score says nothing about the agent;
 * - environment failure: an empty grey striped slot, never a score and never red: ours, not the
 *   agent's;
 * - grading failed: an empty dashed slot that stays: nothing graded it, so there is nothing to draw;
 * - grading: an empty dashed slot, pulsing, until the grade lands.
 *
 * The dashed line is the median of the scored runs, which the server computes over ALL the agent's
 * runs, not just the ones that fit. Each column opens its run. Pointing at a column, or focusing
 * it (the strip is one tab stop; the arrow keys move between runs), opens the run's inspector, and
 * `onInspect` reports its kind so the page's key can light up the matching entry.
 */
export function RunStrip({
  runs,
  median,
  summary,
  onInspect,
  className,
}: {
  runs: RunScore[];
  median: number | null | undefined;
  /** The strip in words, for assistive technology. */
  summary: string;
  onInspect?: (kind: RunKind | null) => void;
  className?: string;
}) {
  const [ref, fit] = useFitCount<HTMLDivElement>(MIN_COLUMN, GAP, MAX_COLUMNS, FALLBACK_COLUMNS);
  const shown = runs.slice(-fit);
  const hidden = runs.length - shown.length;
  // The inspected run and the tab stop, by run id rather than by position, so a run that arrives
  // while the strip is full (shifting every column left) leaves them on the same run.
  const [inspectedId, setInspectedId] = useState<string | null>(null);
  const [stopId, setStopId] = useState<string | null>(null);
  const cells = useRef<(HTMLAnchorElement | null)[]>([]);
  const indexOf = (id: string | null) => (id === null ? -1 : shown.findIndex((r) => r.run_id === id));

  // The one tab stop: the run last focused, else (or once it no longer fits) the newest.
  const held = indexOf(stopId);
  const tabStop = held >= 0 ? held : shown.length - 1;
  const inspected = indexOf(inspectedId);
  const inspect = (i: number | null) => {
    setInspectedId(i === null ? null : shown[i].run_id);
    onInspect?.(i === null ? null : runGlyph(shown[i]).kind);
  };
  const focus = (i: number) => {
    const target = Math.max(0, Math.min(shown.length - 1, i));
    setStopId(shown[target]?.run_id ?? null);
    cells.current[target]?.focus();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, () => void> = {
      ArrowRight: () => focus(tabStop + 1),
      ArrowLeft: () => focus(tabStop - 1),
      Home: () => focus(0),
      End: () => focus(shown.length - 1),
    };
    const move = moves[e.key];
    if (move) {
      e.preventDefault();
      move();
    }
  };

  const run = inspected >= 0 ? shown[inspected] : null;
  // Open the inspector on the side the column is on, so it never runs off the card.
  const onRight = inspected >= shown.length / 2;

  return (
    <div className={cn("relative", className)}>
      <div
        role="group"
        aria-label={summary}
        ref={ref}
        onKeyDown={onKeyDown}
        className="relative flex h-24 w-full items-end border-b border-border-hover"
        style={{ gap: GAP }}
      >
        {median != null && (
          <div
            data-testid="strip-median"
            aria-hidden
            className="pointer-events-none absolute inset-x-0 z-10 border-t border-dashed border-primary"
            style={{ bottom: `${median * 100}%` }}
          />
        )}
        {shown.map((r, i) => {
          const g = runGlyph(r);
          return (
            <Link
              key={r.run_id}
              ref={(el) => {
                cells.current[i] = el;
              }}
              href={`/runs/result?id=${encodeURIComponent(r.run_id)}`}
              tabIndex={i === tabStop ? 0 : -1}
              aria-label={g.label}
              data-testid="strip-column"
              data-kind={g.kind}
              onMouseEnter={() => inspect(i)}
              onMouseLeave={() => inspect(null)}
              onFocus={() => {
                setStopId(r.run_id);
                inspect(i);
              }}
              onBlur={() => inspect(null)}
              className={cn(
                "flex h-full min-w-0 max-w-4 flex-1 rounded-t-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                inspected === i && "ring-1 ring-heading/70",
              )}
            >
              <StripColumn run={r} />
            </Link>
          );
        })}
      </div>
      <p className="mt-1 text-caption text-text-tertiary" aria-hidden>
        {hidden > 0 ? `latest ${shown.length} of ${runs.length}` : "oldest → newest"}
      </p>
      {run && (
        <RunInspector
          run={run}
          className={cn("absolute top-24 mt-2", onRight ? "right-0" : "left-0")}
        />
      )}
    </div>
  );
}

/** One run's column, filling the slot it is given. Exported so the key draws its examples with
 *  this very component; `small` is the key's size (a smaller corner, to keep the same shape). */
export function StripColumn({ run, small = false }: { run: RunScore; small?: boolean }) {
  const g = runGlyph(run);
  const corner = small ? "rounded-t-xs" : "rounded-t-sm";
  if (g.empty) {
    return (
      <span
        aria-hidden
        data-part={g.kind === "infra_failed" ? "infra" : g.kind}
        className={cn(
          "block h-full w-full border border-b-0 border-dashed",
          corner,
          g.kind === "grading"
            ? "border-border-hover motion-safe:animate-pulse"
            : "border-text-tertiary",
        )}
        style={
          g.kind === "infra_failed"
            ? { background: hatch("color-mix(in srgb, var(--color-heading) 10%, transparent)") }
            : undefined
        }
      />
    );
  }
  const total = g.det + g.judge + g.judgeRange + g.unmeasured;
  const share = (part: number) => `${total ? (part / total) * 100 : 0}%`;
  return (
    <span aria-hidden className="flex h-full w-full flex-col justify-end">
      {/* One clipped bar, so the corner follows whatever is on top: the cap, the judge's share
          or the dashed no-judge outline. A capped bar is never shorter than its cap: a run that
          timed out having scored nothing is the one the cap is for, and at 0% it clipped away. */}
      <span
        data-part="bar"
        className={cn(
          "relative flex w-full flex-col-reverse overflow-hidden",
          g.cap && "min-h-1",
          corner,
          g.grey && "opacity-60 grayscale",
        )}
        style={{ height: `${total * 100}%` }}
      >
        {g.det > 0 && <span className="shrink-0 bg-data" style={{ height: share(g.det) }} />}
        {g.judge > 0 && (
          <span className="shrink-0 bg-chart-4" style={{ height: share(g.judge) }} />
        )}
        {g.judgeRange > 0 && (
          <span
            data-part="judge-range"
            className="shrink-0"
            style={{ height: share(g.judgeRange), background: hatch("var(--color-chart-4)") }}
          />
        )}
        {g.unmeasured > 0 && (
          <span
            data-part="unmeasured"
            className={cn("shrink-0 border border-b-0 border-dashed border-text-tertiary", corner)}
            style={{ height: share(g.unmeasured) }}
          />
        )}
        {g.cap && <span data-part="cap" className="absolute inset-x-0 top-0 h-1 bg-err" />}
      </span>
    </span>
  );
}
