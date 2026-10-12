// Fold a set of same-mission runs into headline comparison numbers (pure, client-side).
// Mirrors summarize() in agent-performance.tsx: partial runs (timeout / operator kill) did NOT end
// on the agent's own terms, so they never count toward the baseline. `trend` is oldest→
// newest overall for the non-partial runs.

export interface RunRow {
  overall: number | null;
  partial: boolean;
  when: string; // ISO timestamp for ordering the trend
}

export interface RunSummary {
  n: number; // scored, non-partial runs
  avgOverall: number | null;
  bestOverall: number | null;
  trend: number[];
}

export function summarizeRuns(rows: RunRow[]): RunSummary {
  const counted = rows.filter((r) => !r.partial && r.overall != null) as {
    overall: number;
    when: string;
  }[];
  const overalls = counted.map((r) => r.overall);
  const byTime = [...counted].sort((a, b) => (a.when ?? "").localeCompare(b.when ?? ""));
  const mean = overalls.length
    ? overalls.reduce((a, b) => a + b, 0) / overalls.length
    : null;
  return {
    n: overalls.length,
    avgOverall: mean,
    bestOverall: overalls.length ? Math.max(...overalls) : null,
    trend: byTime.map((r) => r.overall),
  };
}

// ── Agent-centric aggregation (report §14) ─────────────────────────────────────
// Fold the (already fetched) terminal runs + their recorded results into one row per
// agent, entirely client-side. No new backend field is introduced — every value here
// comes from data the Results feature already loads (runs list + per-run result).

/** One terminal run flattened for per-agent rollup. */
export interface AgentRunRow {
  agentId: string;
  agentName: string;
  overall: number | null; // recorded overall score, when graded
  partial: boolean; // did NOT finish on the agent's own terms (timeout / operator kill)
  completed: boolean; // finished on the agent's own terms (done / completed)
  infraFailed?: boolean; // cut short by an environment failure — not an attempt (classifyRun)
  when: string; // ISO timestamp used for "last run"
}

/** Headline track-record for one agent (report §14 columns). */
export interface AgentPerformanceSummary {
  agentId: string;
  agentName: string;
  runs: number; // total terminal runs
  scored: number; // non-partial runs carrying a recorded score
  avgOverall: number | null;
  bestOverall: number | null;
  completionRate: number | null; // completed / attempts (runs minus infraFailed)
  partialRate: number | null; // partial / attempts
  infraFailed?: number; // runs cut short by an environment failure — in `runs`, in no rate
  lastRun: string | null; // most recent run timestamp (ISO)
}

/**
 * Group flattened runs into one summary per agent, ranked best-average-first
 * (report §14: "Which agent is performing best?"). Agents with no scored runs sink
 * to the bottom; ties break on run count, then name. Score aggregates count only
 * non-partial scored runs (parity with {@link summarizeRuns}). `runs` counts every terminal
 * run; `completionRate` and `partialRate` count the agent's attempts only — a run cut short by
 * an environment failure is not one, so it is counted in `infraFailed` instead (#109).
 */
export function summarizeByAgent(rows: AgentRunRow[]): AgentPerformanceSummary[] {
  const byAgent = new Map<string, AgentRunRow[]>();
  for (const row of rows) {
    const list = byAgent.get(row.agentId);
    if (list) list.push(row);
    else byAgent.set(row.agentId, [row]);
  }

  const summaries = [...byAgent.values()].map((agentRows) => {
    const { agentId, agentName } = agentRows[0];
    const runs = agentRows.length;
    const scoredOveralls = agentRows
      .filter((r) => !r.partial && r.overall != null)
      .map((r) => r.overall as number);
    const completed = agentRows.filter((r) => r.completed).length;
    const partial = agentRows.filter((r) => r.partial).length;
    const infraFailed = agentRows.filter((r) => r.infraFailed).length;
    const attempts = runs - infraFailed;
    const lastRun =
      [...agentRows]
        .map((r) => r.when)
        .filter(Boolean)
        .sort((a, b) => b.localeCompare(a))[0] ?? null;

    return {
      agentId,
      agentName,
      runs,
      scored: scoredOveralls.length,
      avgOverall: scoredOveralls.length
        ? scoredOveralls.reduce((a, b) => a + b, 0) / scoredOveralls.length
        : null,
      bestOverall: scoredOveralls.length ? Math.max(...scoredOveralls) : null,
      completionRate: attempts ? completed / attempts : null,
      partialRate: attempts ? partial / attempts : null,
      infraFailed,
      lastRun,
    };
  });

  return summaries.sort(
    (a, b) =>
      (b.avgOverall ?? -1) - (a.avgOverall ?? -1) ||
      b.runs - a.runs ||
      a.agentName.localeCompare(b.agentName),
  );
}
