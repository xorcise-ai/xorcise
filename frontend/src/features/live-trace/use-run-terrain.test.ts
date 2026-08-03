import { describe, it, expect } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { createQueryWrapper } from "@/test/render";
import type { ResolvedTerrainV2 } from "@/lib/api/types";
import { TERRAIN_POLL_MS, terrainRefetchInterval, useRunTerrain } from "./use-run-terrain";

function terrain(running?: boolean): ResolvedTerrainV2 {
  return {
    run_id: "r1",
    mission_id: "c1",
    attribution: running === undefined ? null : { running, attributed: 0, attributable: 0 },
  } as ResolvedTerrainV2;
}

describe("terrainRefetchInterval", () => {
  it("polls while the run is live, regardless of attribution", () => {
    expect(terrainRefetchInterval(true, undefined)).toBe(TERRAIN_POLL_MS);
    expect(terrainRefetchInterval(true, terrain(false))).toBe(TERRAIN_POLL_MS);
  });

  it("keeps polling a TERMINAL run while attribution is in flight", () => {
    // The lifecycle/backfill attribution runs async at/after terminal — the header indicator must
    // keep refreshing until it drains, not freeze at the first (partial) snapshot.
    expect(terrainRefetchInterval(false, terrain(true))).toBe(TERRAIN_POLL_MS);
  });

  it("stops polling a terminal run once attribution is idle or absent", () => {
    expect(terrainRefetchInterval(false, terrain(false))).toBe(false);
    expect(terrainRefetchInterval(false, terrain())).toBe(false); // attribution null
    expect(terrainRefetchInterval(false, undefined)).toBe(false); // not loaded yet
  });
});

describe("useRunTerrain", () => {
  it("fetches the v2 terrain endpoint (/terrain2), not the v1 /terrain endpoint", async () => {
    let hitV2 = false;
    server.use(
      http.get("*/api/runs/r1/terrain2", () => {
        hitV2 = true;
        return HttpResponse.json({
          run_id: "r1",
          mission_id: "c1",
          summary: null,
          groups: [],
          nodes: [],
          edges: [],
          updates: [],
          attribution: null,
          objective_id: null,
        });
      }),
      // If the hook regressed to the v1 endpoint this would resolve instead — return a body that
      // would make the assertion below fail loudly rather than silently pass.
      http.get("*/api/runs/r1/terrain", () =>
        HttpResponse.json({ run_id: "wrong-endpoint", mission_id: "c1" }),
      ),
    );
    const { result } = renderHook(() => useRunTerrain("r1", false), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.terrain).not.toBeNull());
    expect(hitV2).toBe(true);
    expect(result.current.terrain?.run_id).toBe("r1");
  });
});
