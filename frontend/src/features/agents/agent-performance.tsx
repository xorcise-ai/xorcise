"use client";

import { useState } from "react";

import type { ScoreSummary } from "@/lib/api/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { pct, shortTime } from "@/lib/api/format";
import { PerformanceSummary } from "@/features/results/performance-summary";
import { Skeleton } from "@/components/ui/skeleton";
import { StatTile } from "@/components/ui/stat-tile";
import { asideLine, timedOutLine, type RunKind } from "@/features/results/run-glyph";
import { RunKey } from "@/features/results/run-key";
import { RunLedger, type LedgerRow } from "@/features/results/run-ledger";
import type { AgentRunRow } from "./agent-run-history";

/**
 * Performance summary + runs by mission for one agent (report §9). The headline is the server's
 * summary (GET /results/summary): the median of its scored runs (a timed-out run counts, at what
 * it earned), with every run left out of it counted and named, the same number the agent's card
 * on Results › Agents shows. §9's deterministic/judge averages, over those same runs, follow as
 * {@link StatTile}s when meaningful. Below, the run ledger: every run, one row per mission
 * version.
 */
export function AgentPerformance({
  summary,
  summaryState = "ready",
  ledger,
  ledgerState = "ready",
}: {
  summary: ScoreSummary | null | undefined;
  /** Whether the summary is still on its way, failed to load, or in: with none in, the agent has
   *  no finished run yet. */
  summaryState?: "loading" | "error" | "ready";
  ledger: LedgerRow[];
  /** The same for the ledger's runs: with none in, the agent has no finished run yet. */
  ledgerState?: "loading" | "error" | "ready";
}) {
  // The kind of the run under the pointer or focus in the ledger, so the key can light it up.
  const [activeKind, setActiveKind] = useState<RunKind | null>(null);
  const showSplit = summary?.mean_deterministic != null || summary?.mean_judge != null;
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
              {summary.timed_out > 0 && (
                <p className="text-caption text-text-tertiary">{timedOutLine(summary)}</p>
              )}
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
          ) : summaryState === "loading" ? (
            <PerformanceSummary loading />
          ) : summaryState === "error" ? (
            <p className="text-body text-err">Couldn’t load this agent’s performance.</p>
          ) : (
            <p className="text-body text-text-secondary">No finished runs yet.</p>
          )}
          {showSplit && (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-t border-border pt-4 sm:grid-cols-3 lg:grid-cols-6">
              {summary?.mean_deterministic != null && (
                <StatTile
                  label="Avg deterministic"
                  value={pct(summary.mean_deterministic)}
                  tone="muted"
                />
              )}
              {/* Over the runs the judge scored in full: a judge that never ran left a 0 it never
                  gave, and a partial judge's score is only a lower bound. */}
              {summary?.mean_judge != null && (
                <StatTile label="Avg judge" value={pct(summary.mean_judge)} tone="muted" />
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
              <RunKey
                view="ledger"
                active={activeKind}
                bare
                className="border-b border-border pb-3"
              />
              <RunLedger rows={ledger} onInspect={setActiveKind} />
            </>
          ) : ledgerState === "loading" ? (
            <div role="status" aria-label="Loading runs by mission…">
              <Skeleton className="h-16 w-full" />
            </div>
          ) : ledgerState === "error" ? (
            <p className="text-body text-err">Couldn’t load this agent’s runs by mission.</p>
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
