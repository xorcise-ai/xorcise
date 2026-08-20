"use client";

import { useMemo } from "react";
import Link from "next/link";
import { Target } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Reveal } from "@/components/ui/reveal";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/components/ui/cn";
import { useRuns } from "@/features/runs/queries";
import { isTerminal } from "@/features/runs/run-state";
import { useRunResults } from "./queries";
import {
  summarizeByMission,
  type MissionRunRow,
  type MissionPerformanceSummary,
} from "./summarize-missions";
import { PerformanceSummary } from "./performance-summary";
import { LoadingCards } from "./loading-cards";

/**
 * "Missions" secondary view of the Results page (report §14). Re-presents the exact same
 * terminal runs the Agents view folds — but grouped by mission — so an operator can ask
 * "which missions do agents solve well, and which are hard?". Entirely client-side; no new
 * backend field. Reuses <PerformanceSummary> for the tiles.
 */
export function ResultsByMission() {
  const runs = useRuns();

  const completed = useMemo(
    () => (runs.data ?? []).filter(isTerminal),
    [runs.data],
  );
  const results = useRunResults(completed.map((r) => r.run_id));

  const rows: MissionRunRow[] = completed.map((r, i) => {
    const view = results[i]?.data;
    const trigger = r.terminal_trigger;
    // The result view carries the authoritative partial flag; fall back to the run's own
    // trigger (always present) so a still-loading result still classifies (mirrors the Agents view).
    const partial =
      view?.partial ?? (trigger === "timeout" || trigger === "budget");
    const completedOnOwnTerms = trigger === "done" || trigger === "completed";
    return {
      mission: r.mission,
      overall: view?.grade?.overall ?? null,
      partial,
      completed: completedOnOwnTerms,
      when: r.completed_at ?? r.created_at,
    };
  });

  const summaries = useMemo(() => summarizeByMission(rows), [rows]);
  const loadingResults = results.some((q) => q.isLoading);

  return (
    <div className="space-y-4">
      {runs.isError && (
        <p className="text-body text-err">Couldn’t load results.</p>
      )}

      {runs.isLoading && <LoadingCards />}

      {runs.data && completed.length === 0 && <EmptyMissions />}

      {summaries.length > 0 && (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {summaries.map((s, i) => (
              <Reveal key={s.mission} delay={i * 40}>
                <MissionPerformanceCard summary={s} />
              </Reveal>
            ))}
          </div>
          {loadingResults && (
            <p className="text-caption text-text-tertiary">Updating scores…</p>
          )}
        </>
      )}
    </div>
  );
}

function MissionPerformanceCard({
  summary,
}: {
  summary: MissionPerformanceSummary;
}) {
  return (
    <Card className="h-full p-4">
      <div className="flex items-center gap-2">
        <Target className="size-4 shrink-0 text-primary" aria-hidden />
        {/* Card head — 14px/700, matching CardTitle. See the agent card head in
            completed-runs.tsx; the two heads must not drift apart. */}
        <span className="truncate text-row font-bold text-heading">
          {summary.mission}
        </span>
      </div>
      {/* PerformanceSummary reads only the shared numeric fields; adapt the mission summary
          onto its agent-shaped contract so the same tiles render (report §14). Compact (3-up +
          two-line footer) so the labels don't collide and the timestamp doesn't wrap in-card. */}
      <PerformanceSummary
        summary={{
          agentId: summary.mission,
          agentName: summary.mission,
          runs: summary.runs,
          scored: summary.scored,
          avgOverall: summary.avgOverall,
          bestOverall: summary.bestOverall,
          completionRate: summary.completionRate,
          partialRate: summary.partialRate,
          lastRun: summary.lastRun,
        }}
        columns={3}
        className="mt-4"
      />
    </Card>
  );
}

/** No terminal runs yet — what / why / next (report §17). */
function EmptyMissions() {
  return (
    <Card className="flex flex-col items-center gap-3 p-6 text-center">
      <Target className="size-6 text-text-tertiary" aria-hidden />
      <div className="space-y-2">
        <p className="text-body font-bold text-heading">No results yet</p>
        <p className="mx-auto max-w-sm text-body text-text-secondary">
          No mission has a completed evaluation. Start a run and its scores
          will roll up here per mission.
        </p>
      </div>
      <Link href="/runs/new" className={cn(buttonVariants(), "mt-1")}>
        Start run
      </Link>
    </Card>
  );
}
