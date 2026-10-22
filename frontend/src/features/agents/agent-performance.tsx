"use client";

import type { AgentHistoryEntry, ScoreSummary } from "@/lib/api/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { pct, shortTime } from "@/lib/api/format";
import { PerformanceSummary } from "@/features/results/performance-summary";
import { StatTile } from "@/components/ui/stat-tile";
import { judgeDegraded } from "@/features/results/summarize-runs";
import { asideLine } from "@/features/results/run-glyph";
import { RunKey } from "@/features/results/run-key";
import { RunLedger, type LedgerRow } from "@/features/results/run-ledger";
import type { AgentRunRow } from "./agent-run-history";

type Stats = {
  runs: number;
  scored: number;
  noJudge: number; // of `scored`, how many had no judge (summarize-runs.ts judgeDegraded)
  avgOverall: number | null;
  bestOverall: number | null;
  avgJudge: number | null;
  avgDeterministic: number | null;
  partialRate: number;
  trend: number[]; // overall, oldest → newest
};

/** Fold a run history into headline track-record numbers (pure, client-side). */
export function summarize(history: AgentHistoryEntry[]): Stats {
  const runs = history.length;
  const mean = (xs: number[]) =>
    xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  // A partial run (budget timeout or operator/manual kill) did not end on the agent's
  // own terms, so its score must not lower the track record. Score aggregates (avg/best/trend)
  // count only non-partial runs; `runs` and `partialRate` still count every run.
  const counted = history.filter((h) => !h.partial);
  const nums = (pick: (h: AgentHistoryEntry) => number | null | undefined) =>
    counted
      .map(pick)
      .filter((v): v is number => v !== null && v !== undefined);

  const overalls = nums((h) => h.overall);
  const byTime = [...counted].sort((a, b) =>
    (a.created_at ?? "").localeCompare(b.created_at ?? ""),
  );

  return {
    runs,
    scored: overalls.length,
    // Over the same cohort as the average: non-partial runs with a score (#143).
    noJudge: counted.filter((h) => h.overall != null && judgeDegraded(h.judge_status)).length,
    avgOverall: mean(overalls),
    bestOverall: overalls.length ? Math.max(...overalls) : null,
    // A judge that never ran left a 0 it never scored, so it has no place in a judge average
    // (#143). Overall keeps it, by design, and is disclosed instead.
    avgJudge: mean(
      counted
        .filter((h) => !judgeDegraded(h.judge_status))
        .map((h) => h.judge)
        .filter((v): v is number => v !== null && v !== undefined),
    ),
    avgDeterministic: mean(nums((h) => h.deterministic)),
    partialRate: runs ? history.filter((h) => h.partial).length / runs : 0,
    trend: byTime
      .map((h) => h.overall)
      .filter((v): v is number => v !== null && v !== undefined),
  };
}

/**
 * Performance summary + runs by mission for one agent (report §9). The headline is the server's
 * summary (GET /results/summary): the median of the runs that finished on the agent's own terms
 * and were graded with a judge, with every run left out of it counted and named, the same number
 * the agent's card on Results › Agents shows. §9's deterministic/judge averages follow as
 * {@link StatTile}s when meaningful. Below, the run ledger: every run, one row per mission
 * version.
 */
export function AgentPerformance({
  history,
  summary,
  ledger,
}: {
  history: AgentHistoryEntry[];
  summary: ScoreSummary | null | undefined;
  ledger: LedgerRow[];
}) {
  const s = summarize(history);
  const showSplit = s.avgDeterministic !== null || s.avgJudge !== null;
  const aside = summary ? asideLine(summary) : "";

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-4">
          {summary ? (
            <div className="space-y-2">
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
                <StatTile label="Runs" value={String(summary.runs)} />
                <StatTile label="Median" value={pct(summary.median)} tone="primary" />
                <StatTile label="Best" value={pct(summary.best)} />
                <StatTile label="Scored" value={`${summary.scored} of ${summary.runs}`} />
                <StatTile label="Last run" value={shortTime(summary.last_run)} small />
              </dl>
              {aside && (
                <p className="text-caption text-text-tertiary">{aside}: not in the median.</p>
              )}
              {summary.partial_judge > 0 && (
                <p className="text-caption text-text-tertiary">
                  {summary.partial_judge} judged on part of the rubric, counted at the lower
                  bound.
                </p>
              )}
            </div>
          ) : (
            <PerformanceSummary loading />
          )}
          {showSplit && (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-t border-border pt-4 sm:grid-cols-3 lg:grid-cols-6">
              {s.avgDeterministic !== null && (
                <StatTile
                  label="Avg deterministic"
                  value={pct(s.avgDeterministic)}
                  tone="muted"
                />
              )}
              {s.avgJudge !== null && (
                <StatTile
                  label="Avg judge"
                  value={pct(s.avgJudge)}
                  tone="muted"
                />
              )}
            </dl>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-3">
          <p className="text-label uppercase text-text-tertiary">Runs by mission</p>
          {ledger.length > 0 ? (
            <>
              <RunKey view="ledger" bare className="border-b border-border pb-3" />
              <RunLedger rows={ledger} />
            </>
          ) : (
            <p className="text-body text-text-secondary">No finished runs yet.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Mission coverage (report §9) derived from the enriched run rows: how many distinct
 * missions the agent has attempted and completed, and which specialties / difficulties it
 * has been evaluated against. Specialties and difficulty come from the mission catalog join,
 * so they only render when that data is present ("only show meaningful values").
 */
export function AgentCoverage({ rows }: { rows: AgentRunRow[] }) {
  const attempted = new Set(rows.map((r) => r.missionName)).size;
  const completed = new Set(
    rows.filter((r) => r.status.tone === "green").map((r) => r.missionName),
  ).size;
  const specialties = [
    ...new Set(rows.map((r) => r.specialty).filter((v): v is string => !!v)),
  ];
  const difficulties = [
    ...new Set(rows.map((r) => r.proficiency).filter((v): v is string => !!v)),
  ];

  return (
    <Card>
      <CardContent className="space-y-4">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          <StatTile label="Missions attempted" value={String(attempted)} />
          <StatTile
            label="Missions completed"
            value={String(completed)}
            tone="primary"
          />
          {specialties.length > 0 && (
            <StatTile label="Specialties" value={String(specialties.length)} />
          )}
          {difficulties.length > 0 && (
            <StatTile
              label="Difficulty levels"
              value={String(difficulties.length)}
            />
          )}
        </dl>
        {specialties.length > 0 && (
          <ChipRow label="Specialties evaluated" items={specialties} />
        )}
        {difficulties.length > 0 && (
          <ChipRow label="Difficulty coverage" items={difficulties} />
        )}
      </CardContent>
    </Card>
  );
}

function ChipRow({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="space-y-2">
      <p className="text-label uppercase text-text-tertiary">
        {label}
      </p>
      <div className="flex flex-wrap gap-1.5">
        {items.map((it) => (
          <Badge key={it} variant="muted">
            {it}
          </Badge>
        ))}
      </div>
    </div>
  );
}
