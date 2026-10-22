"use client";

import { useMemo } from "react";
import Link from "next/link";
import { ArrowRight, Trophy } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Reveal } from "@/components/ui/reveal";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/components/ui/cn";
import { StatTile } from "@/components/ui/stat-tile";
import { pct, shortTime } from "@/lib/api/format";
import type { RunScore, ScoreSummary } from "@/lib/api/types";
import { useAgents } from "@/features/agents/queries";
import { HarnessGlyph } from "@/features/agents/harnesses";
import { useRunScores, useScoreSummary } from "./queries";
import { asideLine } from "./run-glyph";
import { RunKey } from "./run-key";
import { RunStrip } from "./run-strip";
import { LoadingCards } from "./loading-cards";

/**
 * Agents view of the Results page (report §14) — the default secondary view. One card per agent
 * with its run strip: every recent run drawn as what the checks and the judge each gave it, and
 * the median of the runs that count. The server decides which runs count (GET /results/summary,
 * reporting.scores); the card only draws them. The page header + view tabs live in the
 * <ResultsTabs> shell that renders this.
 */
export function CompletedRuns() {
  const scores = useRunScores();
  const summaries = useScoreSummary("agent");
  const agents = useAgents();

  // Operators think in agent names, not opaque ids (mirrors RunCard / the run filters).
  const agentById = useMemo(
    () => new Map((agents.data ?? []).map((a) => [a.id, a])),
    [agents.data],
  );

  const runsByAgent = useMemo(() => {
    const m = new Map<string, RunScore[]>();
    for (const r of scores.data ?? []) {
      const list = m.get(r.agent_id);
      if (list) list.push(r);
      else m.set(r.agent_id, [r]);
    }
    return m;
  }, [scores.data]);

  // Best median first. An agent with no scored run has no median and sinks; ties go to the agent
  // with more scored runs, then the name.
  const ranked = useMemo(() => {
    const name = (id: string) => agentById.get(id)?.name ?? id.slice(0, 8);
    return [...(summaries.data ?? [])].sort(
      (a, b) =>
        (b.median ?? -1) - (a.median ?? -1) ||
        b.scored - a.scored ||
        name(a.agent_id ?? "").localeCompare(name(b.agent_id ?? "")),
    );
  }, [summaries.data, agentById]);

  const loading = scores.isLoading || summaries.isLoading;
  return (
    <div className="space-y-4">
      {(scores.isError || summaries.isError) && (
        <p className="text-body text-err">Couldn’t load results.</p>
      )}

      {loading && <LoadingCards />}

      {summaries.data && ranked.length === 0 && <EmptyResults />}

      {ranked.length > 0 && <RunKey view="strip" />}

      {ranked.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {ranked.map((s, i) => {
            const id = s.agent_id ?? "";
            const agent = agentById.get(id);
            return (
              <Reveal key={id} delay={i * 40}>
                <AgentPerformanceCard
                  summary={s}
                  runs={runsByAgent.get(id) ?? []}
                  name={agent?.name ?? id.slice(0, 8)}
                  kind={agent?.kind ?? undefined}
                  linked={!!agent}
                />
              </Reveal>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** The card's text alternative to its strip: what a sighted reader takes from it, in words. */
export function stripSummary(name: string, s: ScoreSummary): string {
  const median =
    s.median != null
      ? `median ${pct(s.median)} over ${s.scored} scored ${s.scored === 1 ? "run" : "runs"}`
      : "no scored run yet";
  const aside = asideLine(s);
  return `${name}: ${s.runs} ${s.runs === 1 ? "run" : "runs"}, ${median}${aside ? `; ${aside}, not in the median` : ""}.`;
}

function AgentPerformanceCard({
  summary,
  runs,
  name,
  kind,
  linked,
}: {
  summary: ScoreSummary;
  runs: RunScore[];
  name: string;
  kind?: string;
  linked: boolean;
}) {
  const aside = asideLine(summary);
  const header = (
    <div className="flex items-center justify-between gap-2">
      <span className="flex min-w-0 items-center gap-2">
        <HarnessGlyph kind={kind} className="[&_svg]:!size-5" />
        {/* Card head — 14px/700, the same value CardTitle carries. `row` not `body` because this
            name is one truncating line and body's 1.6 leading only pays for wrapped copy. */}
        <span className="truncate text-row font-bold text-heading">{name}</span>
      </span>
      {linked && (
        <span className="flex shrink-0 items-center gap-1 text-dense text-text-secondary transition-colors group-hover:text-heading">
          View profile
          <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
        </span>
      )}
    </div>
  );

  const body = (
    <>
      {header}
      <RunStrip
        className="mt-4"
        runs={runs}
        median={summary.median}
        summary={stripSummary(name, summary)}
      />
      <dl className="mt-3 grid grid-cols-2 gap-x-3">
        <StatTile label="Median" value={pct(summary.median)} tone="primary" />
        <StatTile label="Scored" value={`${summary.scored} of ${summary.runs}`} small />
      </dl>
      <div className="mt-2 space-y-1 text-caption">
        <p className="text-text-tertiary">Last run {shortTime(summary.last_run)}</p>
        {aside && (
          <p className="text-text-tertiary">
            {aside}: not in the median.
          </p>
        )}
        {summary.partial_judge > 0 && (
          <p className="text-text-tertiary">
            {summary.partial_judge} judged on part of the rubric, counted at the lower bound.
          </p>
        )}
      </div>
    </>
  );

  if (!linked) {
    return <Card className="h-full p-4">{body}</Card>;
  }

  return (
    <Link
      href={`/agents/detail?name=${encodeURIComponent(name)}`}
      className="block h-full"
    >
      <Card className="group h-full p-4 transition-colors hover:border-border-hover">
        {body}
      </Card>
    </Link>
  );
}

/** No terminal runs yet — what / why / next (report §17). */
function EmptyResults() {
  return (
    <Card className="flex flex-col items-center gap-3 p-6 text-center">
      <Trophy className="size-6 text-text-tertiary" aria-hidden />
      <div className="space-y-2">
        <p className="text-body font-bold text-heading">No results yet</p>
        <p className="mx-auto max-w-sm text-body text-text-secondary">
          No agent has completed an evaluation. Start a run and its scores will
          roll up here per agent.
        </p>
      </div>
      <Link href="/runs/new" className={cn(buttonVariants(), "mt-1")}>
        Start run
      </Link>
    </Card>
  );
}
