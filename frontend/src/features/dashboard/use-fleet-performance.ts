import { useMemo } from "react";
import { useRuns } from "@/features/runs/queries";
import { useAgents } from "@/features/agents/queries";
import { useRunResults } from "@/features/results/queries";
import { isTerminal, runPresentation, type RunTone } from "@/features/runs/run-state";
import {
  summarizeByAgent,
  summarizeRuns,
  type AgentPerformanceSummary,
  type AgentRunRow,
  type RunRow,
  type RunSummary,
} from "@/features/results/summarize-runs";
import {
  summarizeByMission,
  type MissionPerformanceSummary,
  type MissionRunRow,
} from "@/features/results/summarize-missions";
import {
  assistMixByMission,
  type AssistMix,
} from "@/features/results/summarize-assist";

/** Terminal runs bucketed by their §10 status tone (completed / partial / failed / other). */
export type ToneMix = Record<RunTone, number>;

export interface FleetPerformance {
  isLoading: boolean;
  isError: boolean;
  resultsLoading: boolean;
  /** Total terminal (evaluated) runs across the fleet. */
  evaluated: number;
  /** Terminal runs by status tone (green = completed, amber = partial, red = failed/timeout). */
  mix: ToneMix;
  /** Agent ids that are still registered (a leaderboard row links to a profile only for these). */
  registeredAgentIds: Set<string>;
  agentSummaries: AgentPerformanceSummary[];
  missionSummaries: MissionPerformanceSummary[];
  fleet: RunSummary;
  /** Per-agent scored-run trend (oldest→newest) for row sparklines. */
  trendByAgent: Map<string, number[]>;
  /** Per-mission scored-run assist mix, so the single-average leaderboard can flag the blend. */
  assistMixByMission: Map<string, AssistMix>;
}

/**
 * Fleet-wide performance for the Dashboard, aggregated ENTIRELY client-side from data the app
 * already fetches — no new endpoint. Reuses the same terminal-runs + per-run-result join the
 * Results page uses (shared ["runs", id, "result"] cache key, so nothing is fetched twice) and the
 * locked aggregators {@link summarizeByAgent}/{@link summarizeByMission}/{@link summarizeRuns}.
 * Centralising the row-mapping here (identical to completed-runs.tsx / results-missions.tsx) means
 * the dashboard widgets don't each re-derive it and can't drift out of the shared partial-run accounting.
 */
export function useFleetPerformance(): FleetPerformance {
  const runs = useRuns();
  const agents = useAgents();
  const terminal = useMemo(() => (runs.data ?? []).filter(isTerminal), [runs.data]);
  const results = useRunResults(terminal.map((r) => r.run_id));
  const resultsLoading = results.some((q) => q.isLoading);
  const agentData = agents.data;

  // Stable signature so the aggregation only recomputes when the underlying scores change, not on
  // every render (useQueries returns a fresh array each time).
  const sig = terminal
    .map(
      (r, i) =>
        `${r.run_id}:${results[i]?.data?.grade?.overall ?? ""}:${results[i]?.data?.partial ?? ""}:${results[i]?.data?.conditions?.intel_disclosed ?? ""}`,
    )
    .join("|");

  return useMemo(() => {
    const nameById = new Map((agentData ?? []).map((a) => [a.id, a.name]));
    const registeredAgentIds = new Set((agentData ?? []).map((a) => a.id));
    const agentRows: AgentRunRow[] = [];
    const chalRows: MissionRunRow[] = [];
    const flat: RunRow[] = [];
    const chalAssistRows: {
      mission: string;
      intel: number;
      overall: number | null;
      partial: boolean;
    }[] = [];
    const mix: ToneMix = { amber: 0, green: 0, red: 0, muted: 0 };

    terminal.forEach((r, i) => {
      const view = results[i]?.data;
      const trigger = r.terminal_trigger;
      // Same classification as completed-runs.tsx / results-missions.tsx: a run that
      // did not end on the agent's own terms is "partial" and is excluded from Average/Best.
      const partial = view?.partial ?? (trigger === "timeout" || trigger === "budget");
      const completed = trigger === "done" || trigger === "completed";
      const overall = view?.grade?.overall ?? null;
      // Assistance level: the run result's recorded intel count (0 = unassisted), fed to
      // assistMixByMission so the mission leaderboard can flag an assisted/unassisted blend.
      const intel = view?.conditions?.intel_disclosed ?? 0;
      const when = r.completed_at ?? r.created_at;
      agentRows.push({
        agentId: r.agent_id,
        agentName: nameById.get(r.agent_id) ?? r.agent_id.slice(0, 8),
        overall,
        partial,
        completed,
        when,
      });
      chalRows.push({ mission: r.mission, overall, partial, completed, when });
      flat.push({ overall, partial, when });
      chalAssistRows.push({ mission: r.mission, intel, overall, partial });
      mix[runPresentation(r.state, r.terminal_trigger).tone] += 1;
    });

    // Per-agent score trend (reuses the locked summarizeRuns fold: non-partial scored, by time).
    const rowsByAgent = new Map<string, AgentRunRow[]>();
    agentRows.forEach((r) => {
      const list = rowsByAgent.get(r.agentId);
      if (list) list.push(r);
      else rowsByAgent.set(r.agentId, [r]);
    });
    const trendByAgent = new Map<string, number[]>();
    rowsByAgent.forEach((rows, id) => trendByAgent.set(id, summarizeRuns(rows).trend));

    return {
      isLoading: runs.isLoading || agents.isLoading,
      isError: runs.isError,
      resultsLoading,
      evaluated: terminal.length,
      mix,
      registeredAgentIds,
      agentSummaries: summarizeByAgent(agentRows),
      missionSummaries: summarizeByMission(chalRows),
      fleet: summarizeRuns(flat),
      trendByAgent,
      assistMixByMission: assistMixByMission(chalAssistRows),
    };
    // `results`/`terminal` are captured via the stable `sig`; agentData identity is a real dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, agentData, runs.isLoading, runs.isError, agents.isLoading, resultsLoading]);
}
