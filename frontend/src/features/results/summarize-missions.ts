// Fold the (already-fetched) terminal runs + their recorded results into one row per
// mission — the "Missions" secondary view of the Results page (report §14). Pure and
// entirely client-side: every value comes from data the Results feature already loads, so
// no new backend field is introduced. Deliberately kept separate from summarizeByAgent()
// in summarize-runs.ts (whose API + tests are locked) while sharing the same accounting:
// partial runs (timeout / budget / kill) did NOT finish on the agent's own terms and so are
// excluded from Average / Best (parity with the shared run accounting), but still count toward Runs / rates.

/** One terminal run flattened for per-mission rollup. */
export interface MissionRunRow {
  mission: string;
  overall: number | null; // recorded overall score, when graded
  partial: boolean; // did NOT finish on the agent's own terms
  completed: boolean; // finished on the agent's own terms (done / completed)
  when: string; // ISO timestamp used for "last run"
}

/** Headline track-record for one mission (report §14 columns, mirroring the agent summary). */
export interface MissionPerformanceSummary {
  mission: string;
  runs: number; // total terminal runs against this mission
  scored: number; // non-partial runs carrying a recorded score
  avgOverall: number | null;
  bestOverall: number | null;
  completionRate: number | null; // completed / runs
  partialRate: number | null; // partial / runs
  lastRun: string | null; // most recent run timestamp (ISO)
}

/**
 * Group flattened runs into one summary per mission, ranked best-average-first (which
 * missions agents solve well vs. struggle on). Missions with no scored run sink to the
 * bottom; ties break on run count, then name.
 */
export function summarizeByMission(
  rows: MissionRunRow[],
): MissionPerformanceSummary[] {
  const byMission = new Map<string, MissionRunRow[]>();
  for (const row of rows) {
    const list = byMission.get(row.mission);
    if (list) list.push(row);
    else byMission.set(row.mission, [row]);
  }

  const summaries = [...byMission.values()].map((chalRows) => {
    const { mission } = chalRows[0];
    const runs = chalRows.length;
    const scoredOveralls = chalRows
      .filter((r) => !r.partial && r.overall != null)
      .map((r) => r.overall as number);
    const completed = chalRows.filter((r) => r.completed).length;
    const partial = chalRows.filter((r) => r.partial).length;
    const lastRun =
      [...chalRows]
        .map((r) => r.when)
        .filter(Boolean)
        .sort((a, b) => b.localeCompare(a))[0] ?? null;

    return {
      mission,
      runs,
      scored: scoredOveralls.length,
      avgOverall: scoredOveralls.length
        ? scoredOveralls.reduce((a, b) => a + b, 0) / scoredOveralls.length
        : null,
      bestOverall: scoredOveralls.length ? Math.max(...scoredOveralls) : null,
      completionRate: runs ? completed / runs : null,
      partialRate: runs ? partial / runs : null,
      lastRun,
    };
  });

  return summaries.sort(
    (a, b) =>
      (b.avgOverall ?? -1) - (a.avgOverall ?? -1) ||
      b.runs - a.runs ||
      a.mission.localeCompare(b.mission),
  );
}
