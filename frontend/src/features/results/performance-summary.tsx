"use client";

import { pct, shortTime } from "@/lib/api/format";
import { cn } from "@/components/ui/cn";
import { Skeleton } from "@/components/ui/skeleton";
import { StatTile, type StatTone } from "@/components/ui/stat-tile";
import type { AgentPerformanceSummary } from "./summarize-runs";

/* StatTile moved to components/ui — it is a design-system atom (the Figma component board
   draws it beside Button and Badge), and four unrelated features were importing it from
   here. Re-exported so the existing import path keeps working for one more pass. */
export { StatTile } from "@/components/ui/stat-tile";

type Tone = StatTone;

/** How many tiles sit on a row. `6` (default) suits wide surfaces (Results agent cards, the
 *  Agent-detail header); `3` suits a narrow container such as a 3-up Agents-list card, where a
 *  six-across grid would collide the labels. */
export type SummaryColumns = 3 | 6;

/** Shared grid (data view + loading skeleton stay pixel-identical — §17 stable sizes). Keyed off
 *  `columns` because the breakpoints are viewport-based and can't see how wide the card actually
 *  is: a 6-across grid overflows inside a narrow card at a wide viewport. */
const GRID_BY_COLUMNS: Record<SummaryColumns, string> = {
  3: "grid grid-cols-3 gap-x-3 gap-y-3",
  6: "grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-6",
};

/** A placeholder tile at the data tile's dimensions — keeps the grid from reflowing on load. */
function StatTileSkeleton() {
  return (
    <div className="flex flex-col gap-1">
      <Skeleton className="h-2.5 w-12" />
      <Skeleton variant="stat" />
    </div>
  );
}

const SKELETON_TILES = 6;
/** The compact layout keeps only the three numeric headline tiles. */
const COMPACT_TILES = 3;

type PerformanceSummaryProps = { columns?: SummaryColumns } & (
  | { loading: true; summary?: undefined; className?: string }
  | { loading?: false; summary: AgentPerformanceSummary; className?: string }
);

/**
 * Reusable performance summary (report §14 fields: Runs · Average · Best · Completion ·
 * Partial · Last run). Pure presentation with a stable, props-driven contract — the caller
 * supplies an already-aggregated {@link AgentPerformanceSummary} (no data fetching inside),
 * so the Results agent cards (§14), the Agents list cards (§7) and the Agent detail header
 * (§9) all render the same tiles. It is agnostic to how the summary was produced: a summary
 * for a single agent renders exactly like one row of the aggregate.
 *
 * Pass `loading` (with no `summary`) to render a stable-size skeleton in the same grid.
 *
 * "Average" and "Best" reflect only runs the agent finished on its own terms (partial runs
 * are excluded upstream in {@link summarizeByAgent}); a "—" simply means no scored run yet.
 */
export function PerformanceSummary(props: PerformanceSummaryProps) {
  const { className, columns = 6 } = props;
  const grid = GRID_BY_COLUMNS[columns];
  // In a narrow card the six-up grid spends a whole extra tile row on three
  // values that are context, not headline — and its widest value (a timestamp)
  // dictates the column width for all five siblings while three "—" cells render
  // 8px of ink in a 130px box. Compact keeps the numeric KPIs as tiles and
  // demotes the rest to one caption line.
  const compact = columns === 3;

  if (props.loading) {
    return (
      <div
        role="status"
        aria-label="Loading performance summary…"
        aria-busy
        className={className}
      >
        <dl className={grid}>
          {Array.from({ length: compact ? COMPACT_TILES : SKELETON_TILES }).map(
            (_, i) => (
              <StatTileSkeleton key={i} />
            ),
          )}
        </dl>
        {compact && <Skeleton className="mt-2 h-2.5 w-40" />}
      </div>
    );
  }

  const { summary } = props;
  const headline: { label: string; value: string; tone?: Tone }[] = [
    { label: "Runs", value: String(summary.runs) },
    { label: "Average", value: pct(summary.avgOverall), tone: "primary" },
    { label: "Best", value: pct(summary.bestOverall) },
  ];

  if (compact) {
    // The footer facts sit on their OWN lines (completion+partial, then last-run) instead of one
    // caption: a single line packing both rates AND a 16-char timestamp wraps mid-value in a
    // ~280px card, stranding "16:04" on a second line. Two short lines never wrap.
    return (
      <div className={className}>
        <dl className={grid}>
          {headline.map((t) => (
            <StatTile
              key={t.label}
              label={t.label}
              value={t.value}
              tone={t.tone}
            />
          ))}
        </dl>
        <div className="mt-2 space-y-1 text-caption text-text-tertiary">
          <p className="flex items-baseline justify-between gap-2">
            <span>Completed · Partial</span>
            <span className="tabular-nums text-text-secondary">
              {pct(summary.completionRate)} · {pct(summary.partialRate)}
            </span>
          </p>
          <p className="flex items-baseline justify-between gap-2">
            <span>Last run</span>
            <span className="tabular-nums text-text-secondary">
              {shortTime(summary.lastRun)}
            </span>
          </p>
        </div>
      </div>
    );
  }

  const tiles: { label: string; value: string; tone?: Tone; small?: boolean }[] =
    [
      ...headline,
      { label: "Completion", value: pct(summary.completionRate) },
      { label: "Partial", value: pct(summary.partialRate), tone: "muted" },
      { label: "Last run", value: shortTime(summary.lastRun), small: true },
    ];

  return (
    <dl className={cn(grid, className)}>
      {tiles.map((t) => (
        <StatTile
          key={t.label}
          label={t.label}
          value={t.value}
          tone={t.tone}
          small={t.small}
        />
      ))}
    </dl>
  );
}
