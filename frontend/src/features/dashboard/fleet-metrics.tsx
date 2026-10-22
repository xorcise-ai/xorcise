"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusDot, type DotTone } from "@/components/ui/dot";
import { StatTile } from "@/components/ui/stat-tile";
import { pct } from "@/lib/api/format";
import { asideLine, timedOutLine } from "@/features/results/run-glyph";
import { useFleetPerformance, type ToneMix } from "./use-fleet-performance";

/**
 * Fleet-wide headline metrics for the Dashboard: how the whole fleet is scoring at a glance
 * (Evaluated / Scored / Median / Best / Pass rate) plus an outcome-mix bar (completed vs partial vs
 * cut short by an environment failure). Pure reuse of {@link useFleetPerformance} and the shared StatTile — no new data.
 */
export function FleetMetrics() {
  const f = useFleetPerformance();

  if (f.isLoading) {
    return (
      <Card className="shrink-0">
        <CardContent className="p-4">
          <Skeleton className="h-14 w-full" />
        </CardContent>
      </Card>
    );
  }

  // Over the agents' attempts: a run an environment failure cut short is not one (#109).
  const passRate = f.attempts ? f.mix.green / f.attempts : null;
  const fleet = f.fleet;
  const timed = fleet ? timedOutLine(fleet) : "";
  const aside = fleet ? asideLine(fleet) : "";

  return (
    <Card className="shrink-0">
      <CardContent className="space-y-3 p-4">
        {f.evaluated === 0 ? (
          <p className="text-body text-text-secondary">
            No completed evaluations yet — start a run to see fleet performance.
          </p>
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
              <StatTile label="Evaluated" value={String(f.evaluated)} />
              <StatTile label="Scored" value={String(fleet?.scored ?? 0)} />
              <StatTile label="Median" value={pct(fleet?.median)} tone="primary" />
              <StatTile label="Best" value={pct(fleet?.best)} />
              <StatTile label="Pass rate" value={pct(passRate)} />
            </dl>
            {/* The server's summary of every finished run: what is in the median, and what is
                counted apart from it, the same rule as every other view. */}
            {fleet && (timed || aside) && (
              <div className="space-y-1 text-caption text-text-tertiary">
                {timed && <p>{timed}</p>}
                {aside && <p>{aside}: not in the median.</p>}
              </div>
            )}
            <StatusMix mix={f.mix} total={f.evaluated} />
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** A slim proportional bar of the run-status mix — functional colours for status only. */
function StatusMix({ mix, total }: { mix: ToneMix; total: number }) {
  const seg = (n: number, cls: string, label: string) =>
    n > 0 ? (
      <div
        key={label}
        className={cls}
        style={{ width: `${(n / total) * 100}%` }}
        title={`${label}: ${n}`}
        aria-hidden
      />
    ) : null;
  return (
    <div className="space-y-2">
      <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-raised">
        {seg(mix.green, "bg-ok", "Completed")}
        {seg(mix.amber, "bg-primary", "Partial")}
        {seg(mix.red, "bg-err", "Infra failed")}
        {/* "Other" has no status tone, so it takes the neutral --color-muted-foreground.
            Held at /50 in the BAR so a wide neutral slice never out-shouts the functional
            colours beside it; the 8px legend swatch is solid, where it needs the weight. */}
        {seg(mix.muted, "bg-muted-foreground/50", "Other")}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-caption text-text-tertiary">
        <Legend tone="ok" label="Completed" n={mix.green} />
        <Legend tone="primary" label="Partial" n={mix.amber} />
        <Legend tone="err" label="Infra failed" n={mix.red} />
        {mix.muted > 0 && <Legend tone="muted" label="Other" n={mix.muted} />}
      </div>
    </div>
  );
}

function Legend({ tone, label, n }: { tone: DotTone; label: string; n: number }) {
  return (
    <span className="flex items-center gap-1.5">
      {/* `lg` (8px) is the declared legend swatch — the dot sits beside 11px caption text
          and the extra pixel is what keeps it readable there. */}
      <StatusDot tone={tone} size="lg" />
      <span>{label}</span>
      <span className="tabular-nums text-text-secondary">{n}</span>
    </span>
  );
}
