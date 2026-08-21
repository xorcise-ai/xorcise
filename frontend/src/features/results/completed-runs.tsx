"use client";

import { useMemo } from "react";
import Link from "next/link";
import { ArrowRight, Trophy } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Reveal } from "@/components/ui/reveal";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/components/ui/cn";
import { useRuns } from "@/features/runs/queries";
import { isTerminal } from "@/features/runs/run-state";
import { useAgents } from "@/features/agents/queries";
import { HarnessGlyph } from "@/features/agents/harnesses";
import { useRunResults } from "./queries";
import {
  summarizeByAgent,
  type AgentRunRow,
  type AgentPerformanceSummary,
} from "./summarize-runs";
import { PerformanceSummary } from "./performance-summary";
import { LoadingCards } from "./loading-cards";

/**
 * Agents view of the Results page (report §14) — the default secondary view. Agent-centric: it
 * folds the terminal runs and their recorded results — data the Results feature already fetches —
 * into one performance card per agent, ranked best-first, entirely client-side (no new backend
 * field). Individual run scorecards stay reachable from Run History and each agent's profile. The
 * page header + view tabs live in the <ResultsTabs> shell that renders this.
 */
export function CompletedRuns() {
  const runs = useRuns();
  const agents = useAgents();

  const completed = useMemo(
    () => (runs.data ?? []).filter(isTerminal),
    [runs.data],
  );
  const results = useRunResults(completed.map((r) => r.run_id));

  // Operators think in agent names, not opaque ids (mirrors RunCard / the run filters).
  const agentNameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of agents.data ?? []) m.set(a.id, a.name);
    return m;
  }, [agents.data]);

  // The agent's harness kind → its identity glyph on the card. A run from a deleted agent has no
  // kind (falls back to the neutral mark).
  const agentKindById = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of agents.data ?? []) if (a.kind) m.set(a.id, a.kind);
    return m;
  }, [agents.data]);

  const rows: AgentRunRow[] = completed.map((r, i) => {
    const view = results[i]?.data;
    const trigger = r.terminal_trigger;
    // The result view carries the authoritative partial flag; fall back to the run's own
    // trigger (always present) so a still-loading / unrecorded result still classifies.
    const partial =
      view?.partial ?? (trigger === "timeout" || trigger === "budget");
    const completedOnOwnTerms = trigger === "done" || trigger === "completed";
    return {
      agentId: r.agent_id,
      agentName: agentNameById.get(r.agent_id) ?? r.agent_id.slice(0, 8),
      overall: view?.grade?.overall ?? null,
      partial,
      completed: completedOnOwnTerms,
      when: r.completed_at ?? r.created_at,
    };
  });

  const summaries = useMemo(() => summarizeByAgent(rows), [rows]);
  const loadingResults = results.some((q) => q.isLoading);
  // A profile link resolves only for an agent still registered (matched by id); a run from a
  // deleted agent shows its numbers but no dead link.
  const registered = useMemo(
    () => new Set((agents.data ?? []).map((a) => a.id)),
    [agents.data],
  );

  return (
    <div className="space-y-4">
      {runs.isError && (
        <p className="text-body text-err">Couldn’t load results.</p>
      )}

      {runs.isLoading && <LoadingCards />}

      {runs.data && completed.length === 0 && <EmptyResults />}

      {summaries.length > 0 && (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {summaries.map((s, i) => (
              <Reveal key={s.agentId} delay={i * 40}>
                <AgentPerformanceCard
                  summary={s}
                  kind={agentKindById.get(s.agentId)}
                  linked={registered.has(s.agentId)}
                />
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

function AgentPerformanceCard({
  summary,
  kind,
  linked,
}: {
  summary: AgentPerformanceSummary;
  kind?: string;
  linked: boolean;
}) {
  const header = (
    <div className="flex items-center justify-between gap-2">
      <span className="flex min-w-0 items-center gap-2">
        <HarnessGlyph kind={kind} className="[&_svg]:!size-5" />
        {/* Card head — 14px/700, the same value CardTitle carries (Figma sets a card
            head in JetBrains Mono Bold at 14). `row` not `body` because this name is
            one truncating line and body's 1.6 leading only pays for wrapped copy. */}
        <span className="truncate text-row font-bold text-heading">
          {summary.agentName}
        </span>
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
      {/* Compact tiles + a two-line footer: the wide 6-up grid collided its COMPLETION/PARTIAL
          labels and wrapped the timestamp across three lines inside this card width. */}
      <PerformanceSummary summary={summary} columns={3} className="mt-4" />
    </>
  );

  if (!linked) {
    return <Card className="h-full p-4">{body}</Card>;
  }

  return (
    <Link
      href={`/agents/detail?name=${encodeURIComponent(summary.agentName)}`}
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
