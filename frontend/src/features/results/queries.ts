import { useMemo } from "react";
import {
  keepPreviousData,
  useQuery,
  useQueries,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import { useRuns } from "@/features/runs/queries";
import { isTerminal } from "@/features/runs/run-state";
import {
  isNotGraded,
  type RunResultView,
  type RunArtifact,
  type RunStats,
  type RunScore,
  type ScoreSummary,
} from "@/lib/api/types";

/** How often the results view re-polls a terminal-but-ungraded run (202 {status:"grading"}). */
const RESULT_POLL_MS = 3000;

/** Poll while the fetched body is gradeless — grading runs async server-side, so the 202
 *  "grading" body only flips to a real result on a refetch; without polling the "Grading in
 *  progress" spinner never resolves. Stops once the grade lands. An errored query has no data
 *  (a 404 = no result at all) and stays a hard stop, as does a "not_graded" body: a run whose
 *  environment failed is never graded, so there is nothing to wait for. Exported for tests. */
export function resultPollInterval(
  data: RunResultView | undefined,
): number | false {
  if (isNotGraded(data)) return false;
  return data && !data.grade ? RESULT_POLL_MS : false;
}

// GET /runs/{id}/result returns RunResultView { grade, conditions, partial, ... }
// (the result endpoint was reshaped from a bare GradeResult).
export function useRunResult(runId: string) {
  return useQuery({
    queryKey: ["runs", runId, "result"],
    queryFn: () =>
      api.get<RunResultView>(`/runs/${encodeURIComponent(runId)}/result`),
    enabled: !!runId,
    retry: false,
    refetchInterval: (query) => resultPollInterval(query.state.data),
  });
}

/**
 * Re-evaluate a terminal run (POST /runs/{id}/regrade): re-grade its already-sealed evidence with
 * the CURRENT judge settings — no new agent run. The classic use is a run the agent solved whose
 * judge half failed on a config limit; the operator fixes it in Settings, then re-evaluates.
 *
 * The server drops the recorded result and re-grades in the background, so on success we invalidate
 * the run's result + stats (the page then sees the 202 "grading" body and polls for the fresh
 * grade via {@link resultPollInterval}) and the runs list (the agent history / dashboard averages
 * reflect the new score).
 */
export function useRegradeRun(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<{ run_id: string; status: string }>(
        `/runs/${encodeURIComponent(runId)}/regrade`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runs", runId, "result"] });
      qc.invalidateQueries({ queryKey: ["runs", runId, "stats"] });
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["results"] });
    },
  });
}

// GET /runs/{id}/stats returns the per-run telemetry snapshot (tokens/counts/timing) recorded at
// grade time (spec §4.3). A terminal-but-ungraded run 202s (no snapshot yet) — surfaced as a
// gradeless body — so callers treat the data as possibly undefined and degrade the KPI tiles.
export function useRunStats(runId: string) {
  return useQuery({
    queryKey: ["runs", runId, "stats"],
    queryFn: () => api.get<RunStats>(`/runs/${encodeURIComponent(runId)}/stats`),
    enabled: !!runId,
    retry: false,
    // A terminal-ungraded run (mid-grade, or mid-re-evaluate once the result is dropped) 202s a
    // {status:"grading"} placeholder that is NOT a RunStats. Surface it as no-data so the KPI tiles
    // degrade to "—" instead of reading through undefined nested fields, and poll until the real
    // snapshot lands.
    select: (data) => (data && "tokens" in data ? data : undefined),
    refetchInterval: (q) => {
      const d = q.state.data;
      // A run whose environment failed answers a final "not_graded" — no snapshot, ever.
      if (isNotGraded(d)) return false;
      return d && !("tokens" in d) ? RESULT_POLL_MS : false;
    },
  });
}

// GET /runs/{id}/artifacts returns the flag/artifact submissions with their full
// content, for operator review — the result endpoint carries only names.
export function useRunArtifacts(runId: string) {
  return useQuery({
    queryKey: ["runs", runId, "artifacts"],
    queryFn: () =>
      api.get<RunArtifact[]>(`/runs/${encodeURIComponent(runId)}/artifacts`),
    enabled: !!runId,
    retry: false,
  });
}

/**
 * Fetch the recorded result for many runs at once (the Results leaderboard).
 * Shares the per-run ["runs", id, "result"] cache key, so opening a result detail
 * is instant after the table loads. A run with no recorded result resolves to an
 * error (404) and is simply shown without scores.
 */
export function useRunResults(runIds: string[]) {
  return useQueries({
    queries: runIds.map((id) => ({
      queryKey: ["runs", id, "result"],
      queryFn: () =>
        api.get<RunResultView>(`/runs/${encodeURIComponent(id)}/result`),
      retry: false,
    })),
  });
}

/** Narrows the performance routes: an agent id and/or a mission slug. */
export interface ScoreFilter {
  agent?: string;
  mission?: string;
}

function scoreQuery(filter: ScoreFilter, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams(extra);
  if (filter.agent) params.set("agent", filter.agent);
  if (filter.mission) params.set("mission", filter.mission);
  const q = params.toString();
  return q ? `?${q}` : "";
}

/** Re-poll while any run is still being graded, so its column fills in by itself. */
const SCORES_POLL_MS = 5000;

/**
 * Which runs have finished, as a short fingerprint, for the performance queries' keys. A run that
 * finishes, or one deleted, changes it, so every view refetches from the runs list the app already
 * polls, never from a mutation each place would have to remember to invalidate. Undefined until
 * the runs list has answered once, so the views do not fetch twice on load.
 */
function useFinishedRuns(): string | undefined {
  const runs = useRuns();
  return useMemo(() => {
    if (runs.data === undefined) return runs.isError ? "unknown" : undefined;
    // FNV-1a over the finished runs' ids, sorted: order-free, and a fixed size however many.
    let hash = 0x811c9dc5;
    const ids = runs.data.filter(isTerminal).map((r) => r.run_id).sort();
    for (const id of ids) {
      for (let i = 0; i < id.length; i++) {
        hash ^= id.charCodeAt(i);
        hash = Math.imul(hash, 0x01000193);
      }
      hash ^= 0x2c; // the separator, so "ab","c" and "a","bc" differ
      hash = Math.imul(hash, 0x01000193);
    }
    return `${ids.length}:${(hash >>> 0).toString(36)}`;
  }, [runs.data, runs.isError]);
}

/** A query held back until the runs list answers reads as loading, not as an empty answer. */
function waitingOnRuns<Q extends { isLoading: boolean }>(query: Q, waiting: boolean): Q {
  return waiting ? { ...query, isLoading: true } : query;
}

/**
 * GET /results/runs: every finished run with its scores and how it counts (`bucket`), oldest
 * first. One request for the whole page, instead of one `/result` per run, and the server decides
 * how each run counts (reporting.scores), so no view counts a run on its own.
 */
export function useRunScores(filter: ScoreFilter = {}, enabled = true) {
  const finished = useFinishedRuns();
  const query = useQuery({
    queryKey: ["results", "runs", filter.agent ?? "", filter.mission ?? "", finished],
    queryFn: () => api.get<RunScore[]>(`/results/runs${scoreQuery(filter)}`),
    enabled: enabled && finished !== undefined,
    // A finished or deleted run refetches under a new key: keep the last answer on screen meanwhile.
    placeholderData: keepPreviousData,
    refetchInterval: (q) =>
      q.state.data?.some((r) => r.bucket === "grading") ? SCORES_POLL_MS : false,
  });
  return waitingOnRuns(query, enabled && finished === undefined);
}

/** GET /results/summary: finished runs summarised per agent, mission version, or agent on a
 *  mission version. The median is over `scored` runs only; everything else is counted apart.
 *  Groups with an agent come back ranked and named (`agent_name`): show them in that order. */
export function useScoreSummary(
  by: "all" | "agent" | "mission" | "mission_agent",
  filter: ScoreFilter = {},
  enabled = true,
) {
  const finished = useFinishedRuns();
  const query = useQuery({
    queryKey: ["results", "summary", by, filter.agent ?? "", filter.mission ?? "", finished],
    queryFn: () => api.get<ScoreSummary[]>(`/results/summary${scoreQuery(filter, { by })}`),
    enabled: enabled && finished !== undefined,
    placeholderData: keepPreviousData,
    refetchInterval: (q) => (q.state.data?.some((s) => s.grading > 0) ? SCORES_POLL_MS : false),
  });
  return waitingOnRuns(query, enabled && finished === undefined);
}
