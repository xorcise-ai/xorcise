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
          className="flex items-center gap-1 text-label uppercase text-text-tertiary transition-colors hover:text-foreground"
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

/** One ranked row: rank · name (+ score bar) · runs · avg%. Wrapped in a Link when href is set. */
function LeaderRow({
  rank,
  name,
  runs,
  avg,
  href,
  trend,
  assist,
}: {
  rank: number;
  name: string;
  runs: number;
  avg: number | null;
  href?: string;
  /** When present (≥1 point), a score-trend sparkline replaces the flat average bar. */
  trend?: number[];
  /** Scored-run assist mix. When any run got intel, the row flags it so the single average is not
   * read as a like-for-like comparison (assisted and unassisted runs are blended into it). */
  assist?: { unassisted: number; assisted: number };
}) {
  const scored = assist ? assist.unassisted + assist.assisted : 0;
  const body = (
    <div className="flex items-center gap-3 px-4 py-2">
      <span className="w-4 shrink-0 text-right font-mono text-caption text-text-tertiary">
        {rank}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-row text-foreground">{name}</span>
        {assist && assist.assisted > 0 && (
          <span className="mt-1 block text-caption text-text-tertiary">
            avg blends {assist.assisted}/{scored} assisted
          </span>
        )}
        {trend && trend.length > 0 ? (
          <Sparkline values={trend} className="mt-1 h-4 w-full text-primary" />
        ) : (
          <Progress className="mt-1" value={Math.round((avg ?? 0) * 100)} />
        )}
      </span>
      <span className="shrink-0 text-caption tabular-nums text-text-tertiary">
        {runs} {runs === 1 ? "run" : "runs"}
      </span>
      <span className="shrink-0 text-row font-semibold tabular-nums text-primary">
        {pct(avg)}
      </span>
    </div>
  );
  return (
    <li>
      {href ? (
        <Link
          href={href}
          className="block transition-colors hover:bg-[rgba(255,255,255,0.02)]"
        >
          {body}
        </Link>
      ) : (
        body
      )}
    </li>
  );
}

/** Agents ranked by average score, best first — reuses the locked summarizeByAgent ordering. */
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
      {ranked.map((s, i) => (
        <LeaderRow
          key={s.agentId}
          rank={i + 1}
          name={s.agentName}
          runs={s.runs}
          avg={s.avgOverall}
          trend={f.trendByAgent.get(s.agentId)}
          href={
            f.registeredAgentIds.has(s.agentId)
              ? `/agents/detail?name=${encodeURIComponent(s.agentName)}`
              : undefined
          }
        />
      ))}
    </Leaderboard>
  );
}

/** Missions ranked by how many times they've been run (most-attempted first). */
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
          key={s.mission}
          rank={i + 1}
          name={s.mission}
          runs={s.runs}
          avg={s.avgOverall}
          assist={f.assistMixByMission.get(s.mission)}
        />
      ))}
    </Leaderboard>
  );
}
