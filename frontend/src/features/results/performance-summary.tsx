"use client";

import { pct, shortTime } from "@/lib/api/format";
import { cn } from "@/components/ui/cn";
import { Skeleton } from "@/components/ui/skeleton";
import { StatTile, type StatTone } from "@/components/ui/stat-tile";
import type { ScoreSummary } from "@/lib/api/types";
import { asideLine, timedOutLine } from "./run-glyph";

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
  | { loading?: false; summary: ScoreSummary; className?: string }
);

/**
 * Reusable performance summary: Runs · Median · Best, then how the agent's runs ended and when it
 * last ran. Pure presentation over the server's {@link ScoreSummary} (GET /results/summary,
 * reporting.scores), so a card says exactly what the Results page, the dashboard and
 * `xorcise leaderboard` say about the same runs.
 *
 * The median is over scored runs (a timed-out run counts, at what it earned). Runs left out of
 * it, and runs that timed out, are named under the tiles, so the number is never read as covering
 * runs it does not.
 *
 * Pass `loading` (with no `summary`) to render a stable-size skeleton in the same grid.
 */
export function PerformanceSummary(props: PerformanceSummaryProps) {
  const { className, columns = 6 } = props;
  const grid = GRID_BY_COLUMNS[columns];
  // In a narrow card the six-up grid spends a whole extra tile row on three values that are
  // context, not headline. Compact keeps the numeric KPIs as tiles and demotes the rest.
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
  // A completion rate over the agent's ATTEMPTS: a run our environment cut short is not one.
  const attempts = summary.runs - summary.infra_failed;
  const completion = attempts ? summary.completed / attempts : null;
  const timed = timedOutLine(summary);
  const aside = asideLine(summary);
  const notes = (
    <>
      {timed && <p className="text-caption text-text-tertiary">{timed}</p>}
      {aside && <p className="text-caption text-text-tertiary">{aside}: not in the median.</p>}
    </>
  );
  const headline: { label: string; value: string; tone?: Tone }[] = [
    { label: "Runs", value: String(summary.runs) },
    { label: "Median", value: pct(summary.median), tone: "primary" },
    { label: "Best", value: pct(summary.best) },
  ];

  if (compact) {
    // The footer facts sit on their OWN lines: one line packing a rate AND a 16-char timestamp
    // wraps mid-value in a ~280px card. Two short lines never wrap.
    return (
      <div className={className}>
        <dl className={grid}>
          {headline.map((t) => (
            <StatTile key={t.label} label={t.label} value={t.value} tone={t.tone} />
          ))}
        </dl>
        <div className="mt-2 space-y-1 text-caption text-text-tertiary">
          <p className="flex items-baseline justify-between gap-2">
            <span>Completed</span>
            <span className="tabular-nums text-text-secondary">{pct(completion)}</span>
          </p>
          <p className="flex items-baseline justify-between gap-2">
            <span>Last run</span>
            <span className="tabular-nums text-text-secondary">{shortTime(summary.last_run)}</span>
          </p>
          {notes}
        </div>
      </div>
    );
  }

  const tiles: { label: string; value: string; tone?: Tone; small?: boolean }[] = [
    ...headline,
    { label: "Scored", value: `${summary.scored} of ${summary.runs}` },
    { label: "Completed", value: pct(completion) },
    { label: "Last run", value: shortTime(summary.last_run), small: true },
  ];
  return (
    <div className={cn("space-y-2", className)}>
      <dl className={grid}>
        {tiles.map((t) => (
          <StatTile key={t.label} label={t.label} value={t.value} tone={t.tone} small={t.small} />
        ))}
      </dl>
      {notes}
    </div>
  );
}
