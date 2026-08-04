"use client";

import { TerrainMapView } from "@/features/live-trace/terrain-map";
import { useMissionTerrain } from "./queries";

/**
 * The ACTUAL terrain map for a mission — the same renderer the live run view draws, fed the
 * mission-scoped projection (infra scaffold + authored mission plane, every element in its
 * base state, no updates). Replaces the old linear Agent → Service flow simplification on
 * the detail page: same layout, legend, pan/zoom and hover popovers as a run's map, so what
 * you study here is exactly what you watch there.
 */
export function MissionTerrain({ missionId }: { missionId: string }) {
  const terrain = useMissionTerrain(missionId);
  return (
    // The map renderer fills its container (the viewport keeps its own 360px floor); a fixed
    // height here keeps the detail page's reading flow — the map is one section, not the page.
    <div className="h-[480px] min-h-0">
      <TerrainMapView
        terrain={terrain.data ?? null}
        isError={terrain.isError}
        resetKey={missionId}
        caption="The map an agent explores during a run — mission nodes start unknown (grey) and color in live · hover a node for details."
      />
    </div>
  );
}
