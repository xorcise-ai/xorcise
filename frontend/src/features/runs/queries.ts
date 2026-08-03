import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import { isTerminal } from "@/features/runs/run-state";
import type {
  RunEntry,
  RunCreate,
  RunCreatedEntry,
  RunEnvironment,
} from "@/lib/api/types";

/** How often the live run view re-polls GET /runs while a run is non-terminal. */
const RUN_POLL_MS = 2000;

/** All runs, newest-first as returned by the server (GET /runs orders created_at DESC), so every
 *  listing — Run History, the dashboard's Recent runs — leads with the most recent run. */
export function useRuns() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: () => api.get<RunEntry[]>("/runs"),
  });
}

/** Single run derived from the list (the server has no GET /runs/{id}).
 *  Polls while the run is non-terminal so the live view reacts to a run
 *  completing, timing out, or being terminated without a manual refresh, and
 *  stops polling once the run reaches a terminal state. */
export function useRun(runId: string) {
  return useQuery({
    queryKey: ["runs"],
    queryFn: () => api.get<RunEntry[]>("/runs"),
    select: (runs) => runs.find((r) => r.run_id === runId) ?? null,
    refetchInterval: (query) => {
      // `query.state.data` is the unselected list; find this run and stop
      // polling once it is terminal. Keep polling while it's missing/active.
      const run = query.state.data?.find((r) => r.run_id === runId);
      return run && isTerminal(run) ? false : RUN_POLL_MS;
    },
  });
}

/** The agent prompt for a run (GET /runs/{id}/prompt) — what the operator pastes into their agent
 *  to start it. Available as soon as the run exists. *launchMode* rebases the run-control
 *  Base URL (and the container-only --add-host note) to where the agent runs — "host" (localhost)
 *  vs "container" (host.docker.internal) — so it tracks the same toggle as the launch profile
 *  instead of staying frozen at the baked host. The mode is part of the query key so flipping the
 *  toggle refetches the matching prompt. */
export function useRunPrompt(runId: string, launchMode: LaunchMode = "host") {
  return useQuery({
    queryKey: ["runs", runId, "prompt", launchMode],
    queryFn: () =>
      api.get<{ run_id: string; prompt: string }>(
        `/runs/${encodeURIComponent(runId)}/prompt?launch_mode=${launchMode}`,
      ),
    enabled: !!runId,
    staleTime: Infinity, // the prompt is fixed for a run+mode; fetch once per mode
  });
}

/** The correlation strategy this run's telemetry profile uses: "resource-attr" =
 *  OTEL_RESOURCE_ATTRIBUTES stamps every OTLP batch (full correlation); "prompt-sentinel" =
 *  best-effort via the prompt marker only. */
export type LaunchProfileCorrelation = "resource-attr" | "prompt-sentinel";

/** Where the agent runs: "host" → the collector endpoint is localhost (a terminal
 *  `claude -p`); "container" → host.docker.internal (a containerized/managed harness). */
export type LaunchMode = "host" | "container";

export interface RunLaunchProfile {
  run_id: string;
  env: Record<string, string>;
  correlation: LaunchProfileCorrelation;
  notes: string[];
  fallback: boolean; // true when no provider matched the run's source_agent (generic fallback)
  launch_mode: LaunchMode;
  // Startup tips (design 2026-07-08): the single-line host launch command (mission substituted +
  // shell-quoted) and the full shell block (env `export`s + command). `command` is null and
  // `shell_block` "" for a launch-agnostic harness.
  command: string | null;
  shell_block: string;
  tips?: string[];
  // Launch modes this harness supports (in preference order). The GUI shows a mode toggle only
  // when there's more than one; a host-only harness (Claude Code) advertises just ["host"].
  launch_modes?: LaunchMode[];
}

/** The harness launch profile for a run (GET /runs/{id}/launch-profile) — the pre-start OTel env
 *  the operator's harness injects into the agent process. Harness-aware: a known harness
 *  (e.g. Claude Code) gets its solid env incl. the run-correlation resource attribute. *launchMode*
 *  picks the collector endpoint — "host" (localhost, the terminal-CLI default) vs "container"
 *  (host.docker.internal). Empty `env` when no collector is configured. */
export function useRunLaunchProfile(runId: string, launchMode: LaunchMode = "host") {
  return useQuery({
    queryKey: ["runs", runId, "launch-profile", launchMode],
    queryFn: () =>
      api.get<RunLaunchProfile>(
        `/runs/${encodeURIComponent(runId)}/launch-profile?launch_mode=${launchMode}`,
      ),
    enabled: !!runId,
    staleTime: Infinity,
  });
}

// Delete a run's result + record. 409 if the run is still active (terminate first);
// surfaces as an ApiError the caller can show. Invalidates the runs list and agent history so the
// deleted run leaves every view.
export function useDeleteRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      api.del(`/runs/${encodeURIComponent(runId)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function useCreateRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RunCreate) => api.post<RunCreatedEntry>("/runs", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });
}

/** Operator-initiated termination of an active run (POST /runs/{id}/terminate). */
export function useTerminateRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      api.post<RunEntry>(`/runs/${encodeURIComponent(runId)}/terminate`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });
}

/** The run's mission-environment state (GET /runs/{id}/environment) — Starting / Ready / Failed /
 *  Released / None. Polls while the run is live so the Environment chip reflects the environment
 *  actually coming up (or dying) without a manual refresh; stops once the run is terminal, where
 *  the state is a fixed "Released". Served from the readiness gate's last observation, so polling
 *  it is cheap (no Docker probe per request). */
export function useRunEnvironment(runId: string, active: boolean) {
  return useQuery({
    queryKey: ["run-environment", runId],
    queryFn: () =>
      api.get<RunEnvironment>(`/runs/${encodeURIComponent(runId)}/environment`),
    enabled: !!runId,
    refetchInterval: active ? RUN_POLL_MS : false,
  });
}
