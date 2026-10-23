import { Card, CardContent } from "@/components/ui/card";
import { StatTile, StatTileRow } from "@/components/ui/stat-tile";
import { pct } from "@/lib/api/format";
import { cn } from "@/components/ui/cn";
import type { RunEntry } from "@/lib/api/types";
import { useRunScores, useScoreSummary } from "./queries";
import { asideLine } from "./run-glyph";

// This run in the context of the same agent's other runs on THIS MISSION VERSION. Answers "is this
// score good for this agent here?", unanswerable from a single number. The median and the trend
// are the server's (GET /results/summary and /results/runs): the same rule as every other view,
// and only runs on this version, whose checks and rubric are the ones this run faced. Which version
// that is, is the server's label on this run's own row, never rebuilt here. Until both answer the
// card shows nothing, rather than "first run" for an agent that has many.
export function CrossRunContext({
  run,
  thisOverall,
}: {
  run: RunEntry;
  thisOverall: number;
}) {
  const filter = { agent: run.agent_id, mission: run.mission };
  const summaries = useScoreSummary("mission_agent", filter);
  const scores = useRunScores(filter);
  const own = scores.data?.find((r) => r.run_id === run.run_id);
  if (!summaries.data || !scores.data || !own) return null;
  const version = own.mission_version;
  const summary = summaries.data.find((s) => s.mission_version === version);
  const trend = scores.data
    .filter((r) => r.mission_version === version && r.bucket === "scored" && r.overall != null)
    .map((r) => r.overall as number);

  // Nothing to compare against yet: this is the agent's first scored run on this version.
  if (!summary || summary.scored <= 1 || summary.median == null) {
    return (
      <Card className="bg-card">
        <CardContent className="p-4">
          <h2 className="mb-2 text-label uppercase text-text-tertiary">
            Cross-run context
          </h2>
          <p className="text-body text-text-secondary">
            First run for this agent on this mission — no prior results to compare against yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  const delta = thisOverall - summary.median;
  const deltaCls =
    delta > 0.001 ? "text-ok" : delta < -0.001 ? "text-err" : "text-text-tertiary";
  const deltaLabel = `${delta >= 0 ? "+" : ""}${Math.round(delta * 100)} pts vs median`;
  const aside = asideLine(summary);

  return (
    <Card className="bg-card">
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-label uppercase text-text-tertiary">
            Cross-run context · this mission
          </h2>
          <span className={cn("text-dense tabular-nums", deltaCls)}>
            {deltaLabel}
          </span>
        </div>
        {/* StatTile renders <dt>/<dd>, so the row is a real <dl>. `primary` marks the ONE
            figure this card is about — the run being read — instead of a local size bump. */}
        <StatTileRow>
          <StatTile label="This run" value={pct(thisOverall)} tone="primary" />
          <StatTile label={`Median (n=${summary.scored})`} value={pct(summary.median)} />
          <StatTile label="Best" value={pct(summary.best)} />
        </StatTileRow>
        {aside && (
          <p className="text-caption text-text-tertiary">{aside}: not in the median.</p>
        )}
        <Trend values={trend} />
      </CardContent>
    </Card>
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
          className="w-2 rounded-full bg-primary/60"
          style={{ height: `${Math.max(2, Math.round((v ?? 0) * 28))}px` }}
          title={pct(v)}
        />
      ))}
    </div>
  );
}
