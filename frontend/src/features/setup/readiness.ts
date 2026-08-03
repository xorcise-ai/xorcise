import { useAgents } from "@/features/agents/queries";
import { useMissions } from "@/features/missions/queries";
import { useSystem } from "@/features/settings/queries";

export interface Readiness {
  /** Any source still loading (no data AND no error) — decide nothing yet. */
  loading: boolean;
  /** All five required gates pass. */
  ready: boolean;
  serverOk: boolean;
  dbOk: boolean;
  dockerOk: boolean;
  agentOk: boolean;
  missionOk: boolean;
  /** Where the "Start a run" CTA should go: /runs/new when ready, else the first
   *  missing prerequisite (agent → mission → /setup for an infra issue) so a
   *  fresh operator never dead-ends on a run form they can't submit. */
  startHref: string;
}

/**
 * The single "are we ready to run?" signal, derived from the same sources the
 * setup checklist uses: system planes + db schema, a registered agent, and an
 * installed-or-catalog-connected mission. `loading` is neutral (no data AND
 * no error) so a cold load never flashes a not-ready verdict.
 */
export function useReadiness(): Readiness {
  const system = useSystem();
  const agents = useAgents();
  const missions = useMissions();

  const sysLoading = !system.data && !system.isError;
  const agentsLoading = !agents.data && !agents.isError;
  const missionsLoading = !missions.data && !missions.isError;
  const loading = sysLoading || agentsLoading || missionsLoading;

  const planes = system.data?.planes ?? [];
  const serverOk = !system.isError;
  const dbOk = system.data
    ? system.data.db_schema === "head" || system.data.db_schema === "fresh"
    : false;
  const dockerOk = planes.find((p) => p.name === "docker")?.ok ?? false;
  const agentOk = (agents.data?.length ?? 0) > 0;
  const installed = (missions.data ?? []).filter((c) => c.installed).length;
  const catalogConnected = system.data?.catalog.state === "connected";
  const missionOk = installed > 0 || catalogConnected;

  const ready = serverOk && dbOk && dockerOk && agentOk && missionOk;
  const startHref = ready
    ? "/runs/new"
    : !agentOk
      ? "/agents"
      : !missionOk
        ? "/missions"
        : "/setup";

  return { loading, ready, serverOk, dbOk, dockerOk, agentOk, missionOk, startHref };
}
