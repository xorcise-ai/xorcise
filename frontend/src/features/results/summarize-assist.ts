// Assist-awareness for the EXISTING per-mission leaderboard. Runs that received different
// amounts of intel assistance must not be silently blended into one average, so the "Most-run
// missions" row can flag when its headline number mixes assisted and unassisted runs.
//
// Kept deliberately separate from the locked single-series aggregators in summarize-runs.ts /
// summarize-missions.ts (their APIs + tests are frozen and other charts depend on them). Shares
// their accounting: only scored, non-partial runs (parity with the shared run accounting) count toward the mix.

/** The fixed assistance buckets, in display order. Unassisted is always index 0 (the anchor); the
 * tail is collapsed at 3 so the set never exceeds 4. */
export const ASSIST_LABELS = ["Unassisted", "1 intel", "2 intel", "3+ intel"] as const;

/** Number of assistance buckets. */
export const ASSIST_BUCKET_COUNT = ASSIST_LABELS.length;

/**
 * Map a run's recorded `conditions.intel_disclosed` to its assistance bucket index (0..3):
 * 0 → Unassisted, 1 → "1 intel", 2 → "2 intel", 3+ → "3+ intel" (tail collapsed). Non-finite or
 * negative counts are treated as unassisted so a bad value can never leak into an assisted bucket.
 */
export function assistBucket(intel: number): number {
  if (!Number.isFinite(intel) || intel <= 0) return 0;
  if (intel >= 3) return ASSIST_BUCKET_COUNT - 1; // 3+ collapses the tail
  return Math.trunc(intel); // 1 or 2
}

/** Scored, non-partial run counts for one mission, split by whether the run got any intel. */
export interface AssistMix {
  unassisted: number; // scored runs with 0 intel
  assisted: number; // scored runs with ≥1 intel
}

/**
 * Per-mission assist mix over scored (non-partial) runs, so the single-average mission
 * leaderboard can flag when its headline number blends assisted and unassisted runs instead of
 * silently averaging across them. Keyed by mission name.
 */
export function assistMixByMission(
  rows: { mission: string; intel: number; overall: number | null; partial: boolean }[],
): Map<string, AssistMix> {
  const mix = new Map<string, AssistMix>();
  for (const r of rows) {
    if (r.partial || r.overall == null) continue; // parity: only scored runs count
    const cur = mix.get(r.mission) ?? { unassisted: 0, assisted: 0 };
    if (assistBucket(r.intel) === 0) cur.unassisted += 1;
    else cur.assisted += 1;
    mix.set(r.mission, cur);
  }
  return mix;
}
