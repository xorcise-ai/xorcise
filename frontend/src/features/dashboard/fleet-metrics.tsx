"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusDot, type DotTone } from "@/components/ui/dot";
import { StatTile } from "@/components/ui/stat-tile";
import { pct } from "@/lib/api/format";
import { useFleetPerformance, type ToneMix } from "./use-fleet-performance";

/**
 * Fleet-wide headline metrics for the Dashboard: how the whole fleet is scoring at a glance
 * (Evaluated / Scored / Average / Best / Pass rate) plus a status-mix bar (completed vs partial vs
 * failed). Pure reuse of {@link useFleetPerformance} and the shared StatTile — no new data.
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

  const passRate = f.evaluated ? f.mix.green / f.evaluated : null;

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
              <StatTile label="Scored" value={String(f.fleet.n)} />
              <StatTile label="Average" value={pct(f.fleet.avgOverall)} tone="primary" />
              <StatTile label="Best" value={pct(f.fleet.bestOverall)} />
              <StatTile label="Pass rate" value={pct(passRate)} />
            </dl>
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
        {seg(mix.red, "bg-err", "Failed / timed out")}
        {/* "Other" has no status tone, so it takes the neutral --color-muted-foreground.
            Held at /50 in the BAR so a wide neutral slice never out-shouts the functional
            colours beside it; the 8px legend swatch is solid, where it needs the weight. */}
        {seg(mix.muted, "bg-muted-foreground/50", "Other")}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-caption text-text-tertiary">
        <Legend tone="ok" label="Completed" n={mix.green} />
        <Legend tone="primary" label="Partial" n={mix.amber} />
        <Legend tone="err" label="Failed" n={mix.red} />
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
