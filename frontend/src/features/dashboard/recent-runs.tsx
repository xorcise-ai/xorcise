"use client";

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { SkeletonRows } from "@/components/ui/skeleton";
import { Reveal } from "@/components/ui/reveal";
import { useRuns } from "@/features/runs/queries";
import { isTerminal, runPresentation, type RunTone } from "@/features/runs/run-state";
import { useAgents } from "@/features/agents/queries";
import { useRunResults } from "@/features/results/queries";
import { pct, shortTime } from "@/lib/api/format";

/** §10 status tone → Badge variant (shared with the Run History cards). */
const toneBadge: Record<RunTone, NonNullable<BadgeProps["variant"]>> = {
  amber: "default",
  green: "ok",
  red: "err",
  muted: "muted",
};

/**
 * Compact recent-runs panel for the landing page (report §4). Each row shows the mission,
 * the agent that ran it, the run status (shared §10 vocabulary), its score when graded, and the
 * time — and the whole row links through to that run. Reuses the shared `useRuns` query plus the
 * per-run results the app already fetches (no new data sources).
 */
/** How many rows the panel offers. The list scrolls inside its own card, so this
 *  is a "how much history is one glance worth" number, not a height constraint —
 *  the old 6 was chosen to fit a band that no longer exists. */
const RECENT_LIMIT = 20;

/**
 * Sizing: `min-h-0 flex-1` is lg-ONLY. In the fixed lg dashboard frame this panel takes the
 * residual height and its row list is the one scroller. Below lg the dashboard is a scrolling
 * document, and this panel must keep its natural height: as the only shrinkable sibling of a
 * ~650px shrink-0 leaderboard rail it used to absorb the entire squeeze and render at zero
 * height, so on a phone the heading was visible with nothing under it.
 */
export function RecentRuns() {
  const runs = useRuns();
  const agents = useAgents();
  const recent = runs.data?.slice(0, RECENT_LIMIT) ?? [];

  // Scores only exist for terminal runs — fetch those (active rows just omit the score).
  const terminalRecent = recent.filter(isTerminal);
  const results = useRunResults(terminalRecent.map((r) => r.run_id));
  const scoreById = new Map<string, number | null>();
  terminalRecent.forEach((r, i) => {
    const v = results[i]?.data;
    if (v) scoreById.set(r.run_id, v.grade?.overall ?? null);
  });

  const agentName = (id: string) =>
    agents.data?.find((a) => a.id === id)?.name ?? id.slice(0, 8);

  return (
    <section className="flex flex-col lg:min-h-0 lg:flex-1">
      <div className="mb-2 flex shrink-0 items-center justify-between">
        <h2 className="text-label uppercase text-text-tertiary">
          Recent runs
        </h2>
        {runs.data && runs.data.length > 0 && (
          <Link
            href="/runs"
            className="flex items-center gap-1 text-label uppercase text-text-tertiary transition-colors hover:text-foreground"
          >
            View all
            <ArrowUpRight className="size-3" />
          </Link>
        )}
      </div>

      {/* At lg the card takes the residual height and the row list is the one scroller.
          Below lg it is capped to most of the viewport so 20 rows cannot bury the rest
          of the dashboard under one endless list. */}
      <Card className="flex max-h-[70vh] flex-col overflow-hidden lg:max-h-none lg:min-h-0 lg:flex-1">
        {runs.isLoading && (
          <CardContent role="status" aria-label="Loading runs…">
            <SkeletonRows count={5} />
          </CardContent>
        )}

        {runs.isError && (
          <CardContent>
            <p className="text-body text-err">Couldn’t load runs.</p>
          </CardContent>
        )}

        {runs.data && runs.data.length === 0 && (
          <CardContent>
            <p className="text-body text-text-secondary">
              No runs yet.{" "}
              <Link href="/agents" className="text-primary underline">
                Register an agent
              </Link>{" "}
              to begin.
            </p>
          </CardContent>
        )}

        {recent.length > 0 && (
          <Reveal className="flex min-h-0 flex-1 flex-col">
            <ul className="min-h-0 flex-1 divide-y divide-border overflow-y-auto">
              {recent.map((run) => {
                const view = runPresentation(run.state, run.terminal_trigger);
                const score = scoreById.get(run.run_id);
                const href =
                  view.action.target === "result"
                    ? `/runs/result?id=${encodeURIComponent(run.run_id)}`
                    : `/runs/live?id=${encodeURIComponent(run.run_id)}`;
                return (
                  <li key={run.run_id}>
                    <Link
                      href={href}
                      className="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-[rgba(255,255,255,0.02)]"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-row text-foreground">
                          {run.mission}
                        </span>
                        <span className="block truncate text-caption text-text-tertiary">
                          {agentName(run.agent_id)}
                        </span>
                      </span>
                      {score != null && (
                        <span className="shrink-0 text-dense font-semibold tabular-nums text-heading">
                          {pct(score)}
                        </span>
                      )}
                      <Badge variant={toneBadge[view.tone]}>{view.label}</Badge>
                      <span className="hidden shrink-0 text-caption tabular-nums text-text-tertiary sm:block">
                        {shortTime(run.created_at)}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </Reveal>
        )}
      </Card>
    </section>
  );
}
