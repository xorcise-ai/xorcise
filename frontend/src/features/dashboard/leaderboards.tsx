"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { Trophy, Target, ArrowUpRight } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { SkeletonRows } from "@/components/ui/skeleton";
import { Reveal } from "@/components/ui/reveal";
import { cn } from "@/components/ui/cn";
import { pct } from "@/lib/api/format";
import { Sparkline } from "./charts";
import type { ScoreSummary } from "@/lib/api/types";
import { useFleetPerformance } from "./use-fleet-performance";

/** Shared leaderboard shell: a titled card whose ranked list is its one internal scroller. */
function Leaderboard({
  icon,
  title,
  className,
  loading,
  emptyLabel,
  empty,
  children,
}: {
  icon: ReactNode;
  title: string;
  className?: string;
  loading: boolean;
  emptyLabel: string;
  empty: boolean;
  children: ReactNode;
}) {
  return (
    <Card className={cn("flex min-h-0 flex-col overflow-hidden", className)}>
      <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-2.5">
        <h2 className="flex items-center gap-2 text-label uppercase text-text-tertiary">
          {icon}
          {title}
        </h2>
        <Link
          href="/results"
          className="flex items-center gap-1 -my-1.5 py-1.5 text-label uppercase text-text-tertiary transition-colors hover:text-foreground"
        >
          View all
          <ArrowUpRight className="size-3" />
        </Link>
      </div>
      {loading ? (
        <div role="status" aria-label="Loading…" className="p-4">
          <SkeletonRows count={5} />
        </div>
      ) : empty ? (
        <p className="p-4 text-body text-text-secondary">{emptyLabel}</p>
      ) : (
        <Reveal className="flex min-h-0 flex-1 flex-col">
          <ol className="min-h-0 flex-1 divide-y divide-border overflow-y-auto">
            {children}
          </ol>
        </Reveal>
      )}
    </Card>
  );
}

/** One ranked row: rank · name (+ score bar) · runs · median%. Wrapped in a Link when href is set.
 *  Everything it says comes from one server summary (reporting.scores). */
function LeaderRow({
  rank,
  name,
  summary,
  href,
  trend,
}: {
  rank: number;
  name: string;
  summary: ScoreSummary;
  href?: string;
  /** When present (≥1 point), a score-trend sparkline replaces the flat median bar. */
  trend?: number[];
}) {
  const { runs, median, scored, assisted, no_judge: noJudge } = summary;
  const body = (
    <div className="flex items-center gap-3 px-4 py-2">
      <span className="w-4 shrink-0 text-right font-mono text-caption text-text-tertiary">
        {rank}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-row text-foreground">{name}</span>
        {/* Assisted and unassisted runs are not like for like: say when the median blends them. */}
        {assisted > 0 && (
          <span className="mt-1 block text-caption text-text-tertiary">
            median blends {assisted}/{scored} assisted
          </span>
        )}
        {noJudge > 0 && (
          <span className="mt-1 block text-caption text-text-tertiary">
            {noJudge} with no judge left out
          </span>
        )}
        {trend && trend.length > 0 ? (
          <Sparkline values={trend} className="mt-1 h-4 w-full text-primary" />
        ) : (
          <Progress className="mt-1" value={Math.round((median ?? 0) * 100)} />
        )}
      </span>
      <span className="shrink-0 text-caption tabular-nums text-text-tertiary">
        {runs} {runs === 1 ? "run" : "runs"}
      </span>
      <span className="shrink-0 text-row tabular-nums text-primary">
        {pct(median)}
      </span>
    </div>
  );
  return (
    <li>
      {href ? (
        <Link
          href={href}
          className="block transition-colors hover:bg-muted"
        >
          {body}
        </Link>
      ) : (
        body
      )}
    </li>
  );
}

/** Agents ranked by median score, best first (the server's summaries, ranked in the hook). */
export function TopAgents({ className }: { className?: string }) {
  const f = useFleetPerformance();
  const ranked = f.agentSummaries.filter((s) => s.scored > 0).slice(0, 8);
  return (
    <Leaderboard
      icon={<Trophy className="size-3.5 text-primary" />}
      title="Top agents"
      className={className}
      loading={f.isLoading}
      empty={ranked.length === 0}
      emptyLabel="No scored runs yet."
    >
      {ranked.map((s, i) => {
        const id = s.agent_id ?? "";
        const name = f.agentName(id);
        return (
          <LeaderRow
            key={id}
            rank={i + 1}
            name={name}
            summary={s}
            trend={f.trendByAgent.get(id)}
            href={
              f.registeredAgentIds.has(id)
                ? `/agents/detail?name=${encodeURIComponent(name)}`
                : undefined
            }
          />
        );
      })}
    </Leaderboard>
  );
}

/** Mission versions ranked by how many times they've been run (most-attempted first). A version
 *  is its own row: a new version can change the checks and the rubric. */
export function TopMissions({ className }: { className?: string }) {
  const f = useFleetPerformance();
  const ranked = [...f.missionSummaries]
    .sort((a, b) => b.runs - a.runs)
    .slice(0, 8);
  return (
    <Leaderboard
      icon={<Target className="size-3.5 text-primary" />}
      title="Most-run missions"
      className={className}
      loading={f.isLoading}
      empty={ranked.length === 0}
      emptyLabel="No mission runs yet."
    >
      {ranked.map((s, i) => (
        <LeaderRow
          key={`${s.mission}@${s.mission_version}`}
          rank={i + 1}
          name={`${s.mission} v${s.mission_version}${s.current_version === false ? " (older)" : ""}`}
          summary={s}
        />
      ))}
    </Leaderboard>
  );
}
