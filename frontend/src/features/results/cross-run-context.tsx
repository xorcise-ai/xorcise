import { Card, CardContent } from "@/components/ui/card";
import { pct } from "@/lib/api/format";
import { cn } from "@/components/ui/cn";
import type { RunEntry } from "@/lib/api/types";
import { useRuns } from "@/features/runs/queries";
import { isTerminal } from "@/features/runs/run-state";
import { useRunResults } from "./queries";
import { summarizeRuns, type RunRow } from "./summarize-runs";

// This run in the context of the same agent's other terminal runs ON THIS MISSION. Answers
// "is this score good for this agent here?" — unanswerable from a single number. Score comparison
// only for the first cut (efficiency deltas are a cheap follow-on now that stats are persisted).
export function CrossRunContext({
  run,
  thisOverall,
}: {
  run: RunEntry;
  thisOverall: number;
}) {
  const runs = useRuns();
  const peers = (runs.data ?? []).filter(
    (r) => r.agent_id === run.agent_id && r.mission === run.mission && isTerminal(r),
  );
  const results = useRunResults(peers.map((p) => p.run_id));

  const rows: RunRow[] = peers.map((p, i) => {
    const view = results[i]?.data;
    return {
      overall: view?.grade?.overall ?? null,
      partial: view?.partial ?? false,
      when: p.completed_at ?? p.created_at,
    };
  });
  const summary = summarizeRuns(rows);

  // Nothing to compare against yet: this is the agent's first (scored) run on this mission.
  if (summary.n <= 1) {
    return (
      <Card className="bg-card">
        <CardContent className="p-4">
          <h2 className="mb-2 text-label uppercase text-text-tertiary">
            Cross-run context
          </h2>
          <p className="max-w-[68ch] text-body text-text-secondary">
            First run for this agent on this mission — no prior results to compare against yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  const avg = summary.avgOverall ?? 0;
  const delta = thisOverall - avg;
  const deltaCls =
    delta > 0.001 ? "text-ok" : delta < -0.001 ? "text-err" : "text-text-tertiary";
  const deltaLabel = `${delta >= 0 ? "+" : ""}${Math.round(delta * 100)} pts vs avg`;

  return (
    <Card className="bg-card">
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-label uppercase text-text-tertiary">
            Cross-run context · this mission
          </h2>
          <span className={cn("text-dense font-semibold tabular-nums", deltaCls)}>
            {deltaLabel}
          </span>
        </div>
        <div className="flex flex-wrap gap-x-8 gap-y-2">
          <Stat label="This run" value={pct(thisOverall)} strong />
          <Stat label={`Avg (n=${summary.n})`} value={pct(summary.avgOverall)} />
          <Stat label="Best" value={pct(summary.bestOverall)} />
        </div>
        <Trend values={summary.trend} />
      </CardContent>
    </Card>
  );
}

function Stat({
  label,
  value,
  strong,
}: {
  label: string;
  value: string;
  strong?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-label uppercase text-text-tertiary">
        {label}
      </span>
      <span
        className={cn(
          "tabular-nums",
          strong ? "text-lg font-bold text-heading" : "text-body font-semibold text-foreground",
        )}
      >
        {value}
      </span>
    </div>
  );
}

// A minimal oldest→newest bar sparkline — pure CSS, no dep. Height encodes the overall score.
function Trend({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  return (
    <div className="flex items-end gap-1" aria-label="score trend, oldest to newest">
      {values.map((v, i) => (
        <div
          key={i}
          className="w-2 rounded-sm bg-primary/60"
          style={{ height: `${Math.max(2, Math.round((v ?? 0) * 28))}px` }}
          title={pct(v)}
        />
      ))}
    </div>
  );
}
