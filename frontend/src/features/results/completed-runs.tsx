"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Trophy } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Reveal } from "@/components/ui/reveal";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/components/ui/cn";
import { pct, shortTime } from "@/lib/api/format";
import type { RunScore, ScoreSummary } from "@/lib/api/types";
import { useAgents } from "@/features/agents/queries";
import { HarnessGlyph } from "@/features/agents/harnesses";
import { useRunScores, useScoreSummary } from "./queries";
import { asideLine, type RunKind } from "./run-glyph";
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
  // The kind of the run under the pointer or focus in any card, so the key can light it up.
  const [activeKind, setActiveKind] = useState<RunKind | null>(null);
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

  // Ranked by the server (best median first, unscored last), so the dashboard, the agent list and
  // `xorcise leaderboard` show the same order.
  const ranked = summaries.data ?? [];

  const loading = scores.isLoading || summaries.isLoading;
  return (
    <div className="space-y-4">
      {(scores.isError || summaries.isError) && (
        <p className="text-body text-err">Couldn’t load results.</p>
      )}

      {loading && <LoadingCards />}

      {summaries.data && ranked.length === 0 && <EmptyResults />}

      {ranked.length > 0 && <RunKey view="strip" active={activeKind} />}

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
                  name={s.agent_name ?? agent?.name ?? id.slice(0, 8)}
                  harness={agent?.kind ?? undefined}
                  linked={!!agent}
                  onInspect={setActiveKind}
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
      ? `median ${pct(s.median)} over ${s.scored} scored ${s.scored === 1 ? "run" : "runs"}${s.timed_out ? ` (${s.timed_out} timed out)` : ""}`
      : "no scored run yet";
  const aside = asideLine(s);
  return `${name}: ${s.runs} ${s.runs === 1 ? "run" : "runs"}, ${median}${aside ? `; ${aside}, not in the median` : ""}.`;
}

/**
 * One agent: its median, its run strip, and one line on what the median covers. Everything else a
 * run carries (its kind, its scores, whether it counts) is in the strip: drawn by each column and
 * spelled out by the inspector, so the card does not repeat it. The agent's name opens its
 * profile; each column opens its run.
 */
function AgentPerformanceCard({
  summary,
  runs,
  name,
  harness,
  linked,
  onInspect,
}: {
  summary: ScoreSummary;
  runs: RunScore[];
  name: string;
  harness?: string;
  linked: boolean;
  onInspect: (kind: RunKind | null) => void;
}) {
  const left = summary.runs - summary.scored;
  return (
    <Card className="flex h-full flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-3">
        <span className="flex min-w-0 items-center gap-2">
          <HarnessGlyph kind={harness} className="[&_svg]:!size-5" />
          {/* Card head — 14px/700, the same value CardTitle carries. */}
          {linked ? (
            <Link
              href={`/agents/detail?name=${encodeURIComponent(name)}`}
              className="truncate rounded-sm text-row font-bold text-heading hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {name}
              <span className="sr-only">: view profile</span>
            </Link>
          ) : (
            <span className="truncate text-row font-bold text-heading">{name}</span>
          )}
        </span>
        <span className="flex shrink-0 flex-col items-end">
          <span className="text-stat font-bold leading-none text-primary">
            {pct(summary.median)}
          </span>
          <span className="mt-1 text-label uppercase text-text-tertiary">median</span>
        </span>
      </div>
      <RunStrip
        runs={runs}
        median={summary.median}
        summary={stripSummary(name, summary)}
        onInspect={onInspect}
      />
      {/* Wraps rather than squeezes: in a narrow card the date drops to its own line. */}
      <div className="mt-auto flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-caption text-text-tertiary">
        <span>
          {summary.scored} of {summary.runs} runs in the median
          {left > 0 && <span className="whitespace-nowrap">{` · ${left} left out`}</span>}
        </span>
        <span className="shrink-0">last run {shortTime(summary.last_run)}</span>
      </div>
    </Card>
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
