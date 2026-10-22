"use client";

import { cn } from "@/components/ui/cn";
import { pct } from "@/lib/api/format";
import type { RunScore } from "@/lib/api/types";
import { hatch, runGlyph } from "./run-glyph";
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
 * judge earned. A judge that never ran leaves its half as a dashed outline (unmeasured, not
 * zero); a judge that scored part of its rubric is solid to its lower bound and hatched to its
 * upper. A run that timed out is drawn at the score it earned, marked ○ beneath, since it is in
 * the median; one an operator stopped is faded and marked ‖, since it is not. An environment
 * failure is an empty slot, never a score. The dashed line is the median of the scored runs, which the server computes over
 * ALL the agent's runs, not just the ones that fit.
 *
 * Decorative to assistive technology: `summary` is read instead, and every run is one click away
 * in the agent's run history.
 */
export function RunStrip({
  runs,
  median,
  summary,
  className,
}: {
  runs: RunScore[];
  median: number | null | undefined;
  summary: string;
  className?: string;
}) {
  const [ref, fit] = useFitCount<HTMLDivElement>(MIN_COLUMN, GAP, MAX_COLUMNS, FALLBACK_COLUMNS);
  const shown = runs.slice(-fit);
  const hidden = runs.length - shown.length;
  return (
    <div className={cn("space-y-1", className)}>
      <div role="img" aria-label={summary} ref={ref} className="w-full">
        <div
          className="relative flex h-24 items-end border-b border-border-hover"
          style={{ gap: GAP }}
          aria-hidden
        >
          {median != null && (
            <div
              data-testid="strip-median"
              className="pointer-events-none absolute inset-x-0 border-t border-dashed border-primary"
              style={{ bottom: `${median * 100}%` }}
            />
          )}
          {shown.map((r) => (
            <StripColumn key={r.run_id} run={r} />
          ))}
        </div>
        <div className="flex h-3.5" style={{ gap: GAP }} aria-hidden>
          {shown.map((r) => {
            const g = runGlyph(r);
            return (
              <span
                key={r.run_id}
                className={cn(
                  "min-w-0 max-w-4 flex-1 text-center text-label leading-3.5",
                  g.mark === "×" && "font-bold text-err",
                  g.mark === "○" && "text-primary",
                  (g.mark === "?" || g.mark === "…" || g.mark === "‖") && "text-text-tertiary",
                )}
              >
                {g.mark}
              </span>
            );
          })}
        </div>
      </div>
      <p className="flex justify-between text-caption text-text-tertiary" aria-hidden>
        <span>{hidden > 0 ? `latest ${shown.length} of ${runs.length}` : "oldest → newest"}</span>
        {median != null && <span className="text-primary">median {pct(median)}</span>}
      </p>
    </div>
  );
}

/** One run's column. Exported so the key draws its examples with this very component. */
export function StripColumn({ run, sample = false }: { run: RunScore; sample?: boolean }) {
  const g = runGlyph(run);
  return (
    <div
      data-testid={sample ? undefined : "strip-column"}
      data-bucket={g.bucket}
      title={sample ? undefined : g.label}
      className={cn(
        "flex h-full min-w-0 max-w-4 flex-1 flex-col-reverse",
        g.faded && "opacity-40",
        g.bucket === "infra_failed" && "rounded-sm border border-dashed border-err/60",
        g.bucket === "grading" &&
          "rounded-sm border border-dashed border-border-hover motion-safe:animate-pulse",
      )}
    >
      {g.det > 0 && <div className="shrink-0 bg-data" style={{ height: `${g.det * 100}%` }} />}
      {g.judge > 0 && (
        <div className="shrink-0 bg-chart-4" style={{ height: `${g.judge * 100}%` }} />
      )}
      {g.judgeRange > 0 && (
        <div
          data-part="judge-range"
          className="shrink-0"
          style={{ height: `${g.judgeRange * 100}%`, background: hatch("var(--color-chart-4)") }}
        />
      )}
      {g.unmeasured > 0 && (
        <div
          data-part="unmeasured"
          className="shrink-0 border border-b-0 border-dashed border-text-tertiary"
          style={{ height: `${g.unmeasured * 100}%` }}
        />
      )}
    </div>
  );
}
