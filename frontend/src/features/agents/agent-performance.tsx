"use client";

import type { AgentHistoryEntry } from "@/lib/api/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { pct } from "@/lib/api/format";
import {
  PerformanceSummary,
  StatTile,
} from "@/features/results/performance-summary";
import type { AgentPerformanceSummary } from "@/features/results/summarize-runs";
import type { AgentRunRow } from "./agent-run-history";

type Stats = {
  runs: number;
  scored: number;
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
    avgOverall: mean(overalls),
    bestOverall: overalls.length ? Math.max(...overalls) : null,
    avgJudge: mean(nums((h) => h.judge)),
    avgDeterministic: mean(nums((h) => h.deterministic)),
    partialRate: runs ? history.filter((h) => h.partial).length / runs : 0,
    trend: byTime
      .map((h) => h.overall)
      .filter((v): v is number => v !== null && v !== undefined),
  };
}

/**
 * Performance summary + performance-over-time for one agent (report §9). The headline tiles
 * reuse the shared <PerformanceSummary> (Runs · Average · Best · Completion · Partial · Last
 * run); §9's extra deterministic/judge averages are appended as {@link StatTile}s and shown
 * only when meaningful. The over-time card renders a real trend chart when ≥2 scored runs
 * exist, else a proper empty state (no decorative flat line).
 */
export function AgentPerformance({
  history,
}: {
  history: AgentHistoryEntry[];
}) {
  const s = summarize(history);
  const partialCount = history.filter((h) => h.partial).length;
  const lastRun = history.length
    ? [...history]
        .map((h) => h.created_at)
        .filter(Boolean)
        .sort((a, b) => b.localeCompare(a))[0]
    : null;

  const summary: AgentPerformanceSummary = {
    agentId: "",
    agentName: "",
    runs: s.runs,
    scored: s.scored,
    avgOverall: s.avgOverall,
    bestOverall: s.bestOverall,
    completionRate: s.runs ? (s.runs - partialCount) / s.runs : null,
    partialRate: s.partialRate,
    lastRun: lastRun ?? null,
  };

  const showSplit = s.avgDeterministic !== null || s.avgJudge !== null;

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-4">
          <PerformanceSummary summary={summary} />
          {showSplit && (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-t border-white/5 pt-4 sm:grid-cols-3 lg:grid-cols-6">
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
          <p className="text-label uppercase text-text-tertiary">
            Score over time
          </p>
          <TrendChart values={s.trend} />
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
            tone="accent"
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

/**
 * Trend chart of overall scores over time (report §9). A real line chart (area + points) once
 * two scored runs exist; otherwise a §17 empty state that says what's missing, why, and what to
 * do next — never a flat decorative line.
 */
function TrendChart({ values }: { values: number[] }) {
  if (values.length < 2) {
    return (
      <div className="rounded-md border border-dashed border-white/10 p-4 text-center">
        <p className="text-body font-medium text-heading">
          Not enough data for a trend yet
        </p>
        <p className="mt-2 text-body text-text-secondary">
          Complete at least two evaluations to display a performance trend.
        </p>
      </div>
    );
  }

  const W = 320;
  const H = 48;
  const step = W / (values.length - 1);
  const y = (v: number) => H - v * H; // scores are 0..1
  const pts = values.map((v, i) => [i * step, y(v)] as const);
  const line = pts.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
  const area = `0,${H} ${line} ${W},${H}`;
  const last = values[values.length - 1];
  const best = Math.max(...values);

  return (
    <div className="space-y-2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-12 w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label={`Overall score over ${values.length} scored runs, oldest to newest`}
      >
        <polygon points={area} fill="var(--color-primary)" fillOpacity="0.12" />
        <polyline
          points={line}
          fill="none"
          stroke="var(--color-primary)"
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
        {pts.map(([px, py], i) => (
          <circle
            key={i}
            cx={px}
            cy={py}
            r={2}
            fill="var(--color-primary)"
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
      <p className="text-caption tabular-nums text-text-tertiary">
        {values.length} scored runs · latest {pct(last)} · best {pct(best)}
      </p>
    </div>
  );
}
