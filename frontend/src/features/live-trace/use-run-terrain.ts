import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { ResolvedTerrainV2 } from "@/lib/api/types";

export const TERRAIN_POLL_MS = 2000;

/** How often to re-fetch the terrain map. Polls while `active` (a live run) so derived state — the
 * OTel collector activating once traces flow, live edges — updates in place. ALSO keeps polling a
 * TERMINAL run while its BYOM attribution is still in flight (`attribution.running`): the lifecycle
 * / backfill attribution runs async at-or-after terminal, so without this the "attributing…"
 * indicator (and the per-span dots) would freeze on the first partial snapshot until a manual
 * reload. Self-limiting: once the drain finishes, `running` flips false and polling stops. */
export function terrainRefetchInterval(
  active: boolean,
  terrain: ResolvedTerrainV2 | undefined,
): number | false {
  if (active) return TERRAIN_POLL_MS;
  return terrain?.attribution?.running ? TERRAIN_POLL_MS : false;
}

/** Fetch the run's resolved v2 terrain map (folded groups/nodes/edges/updates). See
 * `terrainRefetchInterval` for the polling policy. */
export function useRunTerrain(runId: string, active = false) {
  const query = useQuery({
    queryKey: ["terrain", runId],
    queryFn: () => api.get<ResolvedTerrainV2>(`/runs/${encodeURIComponent(runId)}/terrain2`),
    enabled: !!runId,
    refetchInterval: (q) => terrainRefetchInterval(active, q.state.data),
  });
  return {
    terrain: query.data ?? null,
    isLoading: query.isLoading,
    isError: query.isError,
  };
}
