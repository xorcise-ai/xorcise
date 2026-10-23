import { useMemo } from "react";
import type { RunEntry, ScoreSummary } from "@/lib/api/types";
import { useRuns } from "@/features/runs/queries";
import { useAgents } from "@/features/agents/queries";
import { useRunScores, useScoreSummary } from "@/features/results/queries";
import { isTerminal, type RunTone } from "@/features/runs/run-state";

/** Terminal runs bucketed by how they ended (the server's outcome), in the §10 palette:
 *  green = completed, amber = partial, red = cut short by an environment failure, muted = other. */
export type ToneMix = Record<RunTone, number>;

/** A run that ended before its agent started is left out first: it is not the agent's (#187). */
const OUTCOME_TONE: Record<Exclude<NonNullable<RunEntry["outcome"]>, "not_started">, RunTone> = {
  completed: "green",
  partial: "amber",
  infra_failed: "red",
};

/** Where one of the server's summaries is: until it is `ready`, a view drawn from it has nothing
 *  to say about the runs, so it must not read as having no runs. */
export type SummaryState = "loading" | "error" | "ready";

function stateOf(q: { isLoading: boolean; isError: boolean }): SummaryState {
  return q.isLoading ? "loading" : q.isError ? "error" : "ready";
}

export interface FleetPerformance {
  /** The runs list and the agents (the counts and the outcome mix) are still loading. */
  isLoading: boolean;
  /** The runs list failed to load. */
  isError: boolean;
  /** Each score summary's state, apart: the run counts above them may already be in. */
  summaryState: { fleet: SummaryState; agents: SummaryState; missions: SummaryState };
  /** Total terminal (evaluated) runs across the fleet. */
  evaluated: number;
  /** Terminal runs by outcome (green = completed, amber = partial, red = infra failure). */
  mix: ToneMix;
  /** The agents' attempts: terminal runs minus the ones an environment failure cut short. The
   *  pass rate is over these, like every other rate (#109). */
  attempts: number;
  /** Agent ids that are still registered (a leaderboard row links to a profile only for these). */
  registeredAgentIds: Set<string>;
  /** An agent's name, or its short id once it is deleted. */
  agentName: (id: string) => string;
  /** Per agent as the server ranks them: best median first; agents with no scored run sink. */
  agentSummaries: ScoreSummary[];
  /** Per mission version. */
  missionSummaries: ScoreSummary[];
  /** Every finished run in one summary. */
  fleet: ScoreSummary | undefined;
  /** Per-agent scored-run trend (oldest→newest) for row sparklines: the median's own runs. */
  trendByAgent: Map<string, number[]>;
}

/**
 * Fleet-wide performance for the Dashboard. Every score here is the server's
 * (GET /results/summary, reporting.scores), the same summaries the Results page, the agent pages
 * and `xorcise leaderboard` read, so no widget counts a run its own way. Only the outcome mix and
 * the pass rate come from the runs list, by the server's `outcome` on each run.
 */
export function useFleetPerformance(): FleetPerformance {
  const runs = useRuns();
  const agents = useAgents();
  const fleet = useScoreSummary("all");
  const byAgent = useScoreSummary("agent");
  const byMission = useScoreSummary("mission");
  const scores = useRunScores();
  const agentData = agents.data;

  return useMemo(() => {
    const nameById = new Map((agentData ?? []).map((a) => [a.id, a.name]));
    const agentName = (id: string) => nameById.get(id) ?? id.slice(0, 8);
    // A run that ended before its agent started was never evaluated (#187).
    const terminal = (runs.data ?? []).filter(
      (r) => isTerminal(r) && r.outcome !== "not_started",
    );
    const mix: ToneMix = { amber: 0, green: 0, red: 0, muted: 0 };
    // By outcome, not by the status tone: a timeout's tone is red, but it is a partial run.
    for (const r of terminal)
      mix[r.outcome && r.outcome !== "not_started" ? OUTCOME_TONE[r.outcome] : "muted"] += 1;

    const trendByAgent = new Map<string, number[]>();
    for (const r of scores.data ?? []) {
      if (r.bucket !== "scored" || r.overall == null) continue;
      const list = trendByAgent.get(r.agent_id);
      if (list) list.push(r.overall);
      else trendByAgent.set(r.agent_id, [r.overall]);
    }

    // Ranked by the server, the order every view shows.
    const agentSummaries = byAgent.data ?? [];

    return {
      isLoading: runs.isLoading || agents.isLoading,
      isError: runs.isError,
      summaryState: {
        fleet: stateOf(fleet),
        agents: stateOf(byAgent),
        missions: stateOf(byMission),
      },
      evaluated: terminal.length,
      mix,
      attempts: terminal.length - mix.red,
      registeredAgentIds: new Set((agentData ?? []).map((a) => a.id)),
      agentName,
      agentSummaries,
      missionSummaries: byMission.data ?? [],
      fleet: fleet.data?.[0],
      trendByAgent,
    };
  }, [
    runs.data,
    runs.isLoading,
    runs.isError,
    agents.isLoading,
    agentData,
    fleet.data,
    fleet.isLoading,
    fleet.isError,
    byAgent.data,
    byAgent.isLoading,
    byAgent.isError,
    byMission.data,
    byMission.isLoading,
    byMission.isError,
    scores.data,
  ]);
}
