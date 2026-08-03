"use client";

import Link from "next/link";
import { ListChecks } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { SkeletonRows } from "@/components/ui/skeleton";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/components/ui/cn";
import { pct, shortTime } from "@/lib/api/format";
import { useRuns } from "@/features/runs/queries";
import { isTerminal, runPresentation, type RunTone } from "@/features/runs/run-state";
import { useAgents } from "@/features/agents/queries";
import { useRunResults } from "./queries";

/** §10 status tone → the Badge variant carrying the same colour (mirrors the Run History cards). */
const toneBadge: Record<RunTone, NonNullable<BadgeProps["variant"]>> = {
  amber: "default",
  green: "ok",
  red: "err",
  muted: "muted",
};

interface LedgerRow {
  runId: string;
  agentName: string;
  mission: string;
  statusLabel: string;
  tone: RunTone;
  href: string;
  overall: number | null;
  overallUpper: number | null;
  deterministic: number | null;
  judge: number | null;
  judgeUpper: number | null;
  judgePartial: boolean;
  when: string;
}

/**
 * "All Runs" secondary view of the Results page (report §14). Deliberately NOT the Run History
 * cards — that page is the operational view (elapsed / budget / live actions). This is a dense,
 * score-focused **ledger**: one row per run with its Agent, Mission, status, and the recorded
 * Overall / Deterministic / Judge scores, answering §14's "compare every evaluation" purpose.
 * Scores are read from the per-run results the Results feature already fetches (no new backend
 * field); each row links to that run's result (or the live view for a run with no result yet).
 * §17 responsive rule: a real table on ≥sm, stacked rows on narrow screens.
 */
export function ResultsAllRuns() {
  const runs = useRuns();
  const agents = useAgents();
  const all = runs.data ?? [];

  // Results only exist for terminal runs — fetch those (active runs show "—" for scores) so we
  // don't fire a wall of 404s for runs that were never graded.
  const terminal = all.filter(isTerminal);
  const results = useRunResults(terminal.map((r) => r.run_id));
  const viewById = new Map<string, (typeof results)[number]["data"]>();
  terminal.forEach((r, i) => {
    const v = results[i]?.data;
    if (v) viewById.set(r.run_id, v);
  });

  const agentName = (id: string) =>
    agents.data?.find((a) => a.id === id)?.name ?? id.slice(0, 8);

  const rows: LedgerRow[] = [...all]
    .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))
    .map((r) => {
      const view = viewById.get(r.run_id);
      const grade = view?.grade;
      const status = runPresentation(r.state, r.terminal_trigger);
      return {
        runId: r.run_id,
        agentName: agentName(r.agent_id),
        mission: r.mission,
        statusLabel: status.label,
        tone: status.tone,
        href:
          status.action.target === "result"
            ? `/runs/result?id=${encodeURIComponent(r.run_id)}`
            : `/runs/live?id=${encodeURIComponent(r.run_id)}`,
        overall: grade?.overall ?? null,
        overallUpper: grade?.overall_upper ?? null,
        deterministic: grade ? grade.breakdown.deterministic : null,
        judge: grade ? grade.breakdown.judge : null,
        judgeUpper: grade?.judge_upper ?? null,
        judgePartial: grade?.judge_status === "partial",
        when: r.created_at,
      };
    });

  const scoresLoading = results.some((q) => q.isLoading);

  return (
    <div className="space-y-3">
      {runs.isError && <p className="text-body text-err">Couldn’t load runs.</p>}
      {runs.isLoading && <LoadingLedger />}
      {runs.data && rows.length === 0 && <EmptyAllRuns />}

      {rows.length > 0 && (
        <>
          <Card>
            <CardContent className="p-0">
              {/* ≥sm: aligned ledger */}
              <table className="hidden w-full font-mono text-dense sm:table">
                <thead>
                  <tr className="border-b border-white/10 text-left text-label uppercase text-text-tertiary">
                    <th className="px-4 py-2">Agent</th>
                    <th className="px-4 py-2">Mission</th>
                    <th className="px-4 py-2">Status</th>
                    <th className="px-4 py-2 text-right">Overall</th>
                    <th className="px-4 py-2 text-right">
                      Deterministic
                    </th>
                    <th className="px-4 py-2 text-right">Judge</th>
                    <th className="px-4 py-2">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr
                      key={r.runId}
                      className="border-b border-white/5 transition-colors last:border-0 hover:bg-[rgba(232,184,75,0.05)]"
                    >
                      <td className="px-4 py-2.5">
                        <Link
                          href={r.href}
                          className="font-medium text-primary hover:underline"
                        >
                          {r.agentName}
                        </Link>
                      </td>
                      <td className="px-4 py-2.5 text-text-secondary">
                        {r.mission}
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge variant={toneBadge[r.tone]}>{r.statusLabel}</Badge>
                      </td>
                      <td
                        className={cn(
                          "px-4 py-2.5 text-right font-semibold tabular-nums",
                          r.overall != null ? "text-ok" : "text-text-tertiary",
                        )}
                      >
                        {scoreRange(r.overall, r.overallUpper)}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-text-secondary">
                        {pct(r.deterministic)}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-text-secondary">
                        {scoreRange(r.judge, r.judgeUpper)}
                        {r.judgePartial && (
                          <span className="ml-1 text-primary" title="Partial judge result">
                            *
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-text-tertiary">
                        {shortTime(r.when)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* narrow: stacked rows */}
              <ul className="divide-y divide-white/5 sm:hidden">
                {rows.map((r) => (
                  <li key={r.runId}>
                    <Link
                      href={r.href}
                      className="block p-4 transition-colors hover:bg-[rgba(232,184,75,0.05)]"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <span className="min-w-0 truncate font-medium text-primary">
                          {r.agentName}
                        </span>
                        <Badge variant={toneBadge[r.tone]}>{r.statusLabel}</Badge>
                      </div>
                      <p className="mt-1 truncate text-dense text-text-secondary">
                        {r.mission}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-dense tabular-nums text-text-secondary">
                        <span>
                          Overall{" "}
                          <span
                            className={cn(
                              "font-semibold",
                              r.overall != null
                                ? "text-ok"
                                : "text-text-tertiary",
                            )}
                          >
                            {scoreRange(r.overall, r.overallUpper)}
                          </span>
                        </span>
                        <span>Det {pct(r.deterministic)}</span>
                        <span>
                          Judge {scoreRange(r.judge, r.judgeUpper)}
                          {r.judgePartial ? " (partial)" : ""}
                        </span>
                        <span className="text-text-tertiary">
                          {shortTime(r.when)}
                        </span>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
          {scoresLoading && (
            <p className="text-caption text-text-tertiary">Updating scores…</p>
          )}
        </>
      )}
    </div>
  );
}

function scoreRange(lower: number | null, upper: number | null): string {
  if (lower == null) return pct(lower);
  return upper != null && upper > lower + 1e-9
    ? `${pct(lower)}–${pct(upper)}`
    : pct(lower);
}

/** No runs at all — what / why / next (report §17). */
function EmptyAllRuns() {
  return (
    <Card className="flex flex-col items-center gap-3 p-6 text-center">
      <ListChecks className="size-6 text-text-tertiary" aria-hidden />
      <div className="space-y-2">
        <p className="text-body font-semibold text-heading">No runs yet</p>
        <p className="mx-auto max-w-sm text-body text-text-secondary">
          Nothing has been evaluated. Start a run to see it appear here.
        </p>
      </div>
      <Link href="/runs/new" className={cn(buttonVariants(), "mt-1")}>
        Start Run
      </Link>
    </Card>
  );
}

/** Stable-sized skeleton while the ledger loads (report §17). */
function LoadingLedger() {
  return (
    <Card>
      <CardContent role="status" aria-label="Loading runs…" className="py-4">
        <SkeletonRows count={4} />
      </CardContent>
    </Card>
  );
}
