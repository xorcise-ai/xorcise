"use client";

import Link from "next/link";
import type {
  AgentHistoryEntry,
  CatalogEntry,
  RunEntry,
} from "@/lib/api/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { SkeletonRows } from "@/components/ui/skeleton";
import { pct, shortTime } from "@/lib/api/format";
import {
  runPresentation,
  type RunPresentation,
  type RunTone,
} from "@/features/runs/run-state";

/**
 * A run's recorded result (score history) enriched, entirely client-side, with the
 * mission + status it belongs to (report §9). The history payload only carries scores +
 * conditions, so the mission name / specialty / difficulty are joined from the runs list
 * (by run_id) and the mission catalog (by mission_id) the page already fetches — no new
 * backend field. Status reuses the shared §10 {@link runPresentation} vocabulary.
 */
export interface AgentRunRow {
  runId: string;
  missionName: string;
  specialty: string | null;
  proficiency: string | null;
  status: RunPresentation;
  overall: number | null;
  deterministic: number | null;
  judge: number | null;
  partial: boolean;
  when: string; // ISO created_at
}

/** §10 status tone → the Badge variant carrying the same colour (mirrors the Run History cards). */
const toneBadge: Record<RunTone, NonNullable<BadgeProps["variant"]>> = {
  amber: "default",
  green: "ok",
  red: "err",
  muted: "muted",
};

/**
 * Fold an agent's score history into presentable rows, joining the runs list (mission +
 * live state) and the mission catalog (specialty + difficulty). Pure and defensive: when a
 * run is no longer in the list its status is reconstructed from the history entry's own
 * `partial` flag/trigger so the row still reads correctly. Newest run first.
 */
export function buildAgentRunRows(
  history: AgentHistoryEntry[],
  runs: RunEntry[] = [],
  catalog: CatalogEntry[] = [],
): AgentRunRow[] {
  const runById = new Map(runs.map((r) => [r.run_id, r]));
  const catById = new Map(catalog.map((c) => [c.mission_id, c]));

  return history
    .map((h): AgentRunRow => {
      const run = runById.get(h.run_id);
      const cat = run ? catById.get(run.mission) : undefined;
      const status = run
        ? runPresentation(run.state, run.terminal_trigger)
        : runPresentation(
            "terminal",
            h.partial ? (h.partial_trigger ?? "budget") : "done",
          );
      return {
        runId: h.run_id,
        missionName: cat?.name ?? run?.mission ?? "Unknown mission",
        specialty: cat?.specialty ?? null,
        proficiency: cat?.proficiency ?? null,
        status,
        overall: h.overall,
        deterministic: h.deterministic,
        judge: h.judge,
        partial: h.partial,
        when: h.created_at,
      };
    })
    .sort((a, b) => (b.when ?? "").localeCompare(a.when ?? ""));
}

function resultHref(runId: string): string {
  return `/runs/result?id=${encodeURIComponent(runId)}`;
}

/**
 * Compact run history (report §9) — a scannable table rather than one large card per run.
 * Columns: Mission · Status · Overall · Deterministic · Judge · Date. The mission name in
 * each row links through to that run's result, so the "view result" capability is preserved.
 * §17 responsive rule: a real table on ≥sm, stacked rows on narrow screens.
 */
export function AgentRunHistory({
  rows,
  isLoading = false,
  isError = false,
}: {
  rows: AgentRunRow[];
  isLoading?: boolean;
  isError?: boolean;
}) {
  if (isLoading)
    return (
      <div role="status" aria-label="Loading history…">
        <SkeletonRows count={4} />
      </div>
    );
  if (isError)
    return <p className="text-body text-err">Couldn’t load history.</p>;
  if (rows.length === 0)
    return (
      <Card>
        <CardContent className="space-y-2 text-center text-body text-text-secondary">
          <p className="text-heading">No runs yet for this agent.</p>
          <p>Start a run to begin building a track record.</p>
        </CardContent>
      </Card>
    );

  return (
    <Card>
      <CardContent className="p-0">
        {/* ≥sm: aligned table */}
        <table className="hidden w-full text-body sm:table">
          <thead>
            <tr className="border-b border-white/10 text-left text-label uppercase text-text-tertiary">
              <th className="px-4 py-2 font-medium">Mission</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 text-right font-medium">Overall</th>
              <th className="px-4 py-2 text-right font-medium">Deterministic</th>
              <th className="px-4 py-2 text-right font-medium">Judge</th>
              <th className="px-4 py-2 font-medium">Date</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.runId}
                className="border-b border-white/5 last:border-0 hover:bg-white/[0.03]"
              >
                <td className="px-4 py-2.5">
                  <Link
                    href={resultHref(r.runId)}
                    className="font-medium text-primary hover:underline"
                  >
                    {r.missionName}
                  </Link>
                  {(r.specialty || r.proficiency) && (
                    <span className="ml-2 text-caption text-text-tertiary">
                      {[r.specialty, r.proficiency].filter(Boolean).join(" · ")}
                    </span>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  <Badge variant={toneBadge[r.status.tone]}>
                    {r.status.label}
                  </Badge>
                </td>
                <td className="px-4 py-2.5 text-right font-semibold tabular-nums text-heading">
                  {pct(r.overall)}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-text-secondary">
                  {pct(r.deterministic)}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-text-secondary">
                  {pct(r.judge)}
                </td>
                <td className="px-4 py-2.5 text-caption text-text-tertiary">
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
                href={resultHref(r.runId)}
                className="block p-4 hover:bg-white/[0.03]"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="min-w-0 truncate font-medium text-primary">
                    {r.missionName}
                  </span>
                  <Badge variant={toneBadge[r.status.tone]}>
                    {r.status.label}
                  </Badge>
                </div>
                <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-dense text-text-secondary tabular-nums">
                  <span>
                    Overall{" "}
                    <span className="font-semibold text-heading">
                      {pct(r.overall)}
                    </span>
                  </span>
                  <span>Det {pct(r.deterministic)}</span>
                  <span>Judge {pct(r.judge)}</span>
                  <span className="text-text-tertiary">{shortTime(r.when)}</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
