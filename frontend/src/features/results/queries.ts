import {
  useQuery,
  useQueries,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { RunResultView, RunArtifact, RunStats } from "@/lib/api/types";

/** How often the results view re-polls a terminal-but-ungraded run (202 {status:"grading"}). */
const RESULT_POLL_MS = 3000;

/** Poll while the fetched body is gradeless — grading runs async server-side, so the 202
 *  "grading" body only flips to a real result on a refetch; without polling the "Grading in
 *  progress" spinner never resolves. Stops once the grade lands. An errored query has no data
 *  (a 404 = no result at all) and stays a hard stop. Exported for tests. */
export function resultPollInterval(
  data: RunResultView | undefined,
): number | false {
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
