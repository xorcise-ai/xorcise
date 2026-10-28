"use client";

import { cn } from "@/components/ui/cn";
import { shortTime } from "@/lib/api/format";
import type { RunScore } from "@/lib/api/types";
import { KIND_META, kindHeading, runCells, runKind } from "./run-glyph";

/**
 * What one run is, in words: its kind and whether it is in the median, why, and its scores. Opens
 * beside the run under the pointer or keyboard focus in the run strip, the run ledger and the
 * agreement plot, so a reader never has to decode a shape from the key. Floating and
 * non-interactive: the run itself is the link.
 */
export function RunInspector({
  run,
  agent,
  className,
  style,
}: {
  run: RunScore;
  /** Who ran it, where the view mixes agents (the agreement plot). */
  agent?: string;
  className?: string;
  style?: React.CSSProperties;
}) {
  const kind = runKind(run);
  const meta = KIND_META[kind];
  const cells = runCells(run);
  return (
    <div
      data-testid="run-inspector"
      aria-hidden
      className={cn(
        "pointer-events-none z-20 w-60 max-w-[calc(100vw-2rem)] space-y-1.5 rounded-md border border-border-hover bg-raised p-3 shadow-lg",
        className,
      )}
      style={style}
    >
      {agent && <p className="text-caption font-bold text-heading">{agent}</p>}
      <p
        className={cn(
          "text-caption font-bold",
          kind === "timed_out"
            ? "text-err"
            : meta.inMedian
              ? "text-primary"
              : "text-text-secondary",
        )}
      >
        {kindHeading(kind)}
      </p>
      <p className="text-caption text-foreground">{meta.body}</p>
      <dl className="grid grid-cols-3 gap-1.5 border-t border-border pt-1.5">
        <div>
          <dt className="text-label uppercase text-text-tertiary">Checks</dt>
          <dd className="text-dense font-bold text-data">{cells.checks}</dd>
        </div>
        <div>
          <dt className="text-label uppercase text-text-tertiary">Judge</dt>
          <dd className="text-dense font-bold text-chart-4">{cells.judge}</dd>
        </div>
        <div>
          <dt className="text-label uppercase text-text-tertiary">Overall</dt>
          <dd className="text-dense font-bold text-heading">{cells.overall}</dd>
        </div>
      </dl>
      <p className="text-label text-text-tertiary">
        {shortTime(run.completed_at ?? run.created_at)} · open to see the run
      </p>
    </div>
  );
}
