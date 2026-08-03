import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  ConfigView,
  JudgeTestResult,
  ModelConfigUpdate,
  NetworkConfigUpdate,
  SystemInfo,
  TerrainModelConfigUpdate,
} from "@/lib/api/types";

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: () => api.get<ConfigView>("/config"),
  });
}

/**
 * The system probe — role, per-module reachability, DB schema, catalog.
 *
 * Polled because the status bar renders it on EVERY page and a module that went away should
 * not keep reading green until someone reloads. 15s is comfortably affordable: the server
 * memoises the two `docker` subprocess probes and the remote catalog round-trip for 10s, which
 * took this endpoint from ~1.02s cold to ~0.05s warm (measured 2026-07-28). Without that cache
 * this interval would be a self-inflicted load problem.
 */
export function useSystem() {
  return useQuery({
    queryKey: ["system"],
    queryFn: () => api.get<SystemInfo>("/system"),
    refetchInterval: 15_000,
  });
}

/** Set the BYOM judge model; the server echoes the fresh (masked) config view. */
export function useSaveModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (update: ModelConfigUpdate) =>
      api.put<ConfigView>("/config/model", update),
    onSuccess: (view) => {
      qc.setQueryData(["config"], view);
      qc.invalidateQueries({ queryKey: ["config"] });
    },
  });
}

/** Set (or clear, via empty strings) the terrain attribution model override. */
export function useSaveTerrainModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (update: TerrainModelConfigUpdate) =>
      api.put<ConfigView>("/config/terrain-model", update),
    onSuccess: (view) => {
      qc.setQueryData(["config"], view);
      qc.invalidateQueries({ queryKey: ["config"] });
    },
  });
}

/** Live-test the saved judge key by actually calling the model (POST /config/model/test). */
export function useTestModel() {
  return useMutation({
    mutationFn: () => api.post<JudgeTestResult>("/config/model/test"),
  });
}

/** Live-test the terrain attribution model — the custom override if set, else the judge trio
 *  (POST /config/terrain-model/test). */
export function useTestTerrainModel() {
  return useMutation({
    mutationFn: () => api.post<JudgeTestResult>("/config/terrain-model/test"),
  });
}

/** Lifecycle of the unified save→verify flow: one phase drives the single
 *  inline ConnectionStatus line and the card's header badge. */
export type ConnectPhase =
  | "idle"
  | "saving"
  | "testing"
  | "connected"
  | "save_error"
  | "test_error"
  | "not_configured";

/** Compose save + live-test into one lifecycle: saving a model config
 *  immediately verifies the connection with a real call, so both model cards
 *  share the Saving… → Verifying connection… → Connected progression.
 *  `testOnly` re-checks the saved config through the same phase (Test button). */
export function useSaveAndTest(kind: "judge" | "terrain") {
  const saveJudge = useSaveModel();
  const saveTerrain = useSaveTerrainModel();
  const testJudge = useTestModel();
  const testTerrain = useTestTerrainModel();
  // Both test mutations share a signature, so the union is directly callable.
  const test = kind === "judge" ? testJudge : testTerrain;
  const [phase, setPhase] = useState<ConnectPhase>("idle");

  function testOnly() {
    setPhase("testing");
    test.mutate(undefined, {
      onSuccess: (result) =>
        setPhase(
          result.ok
            ? "connected"
            : result.status === "not_configured"
              ? "not_configured"
              : "test_error",
        ),
      onError: () => setPhase("test_error"),
    });
  }

  function saveAndTest(
    update: ModelConfigUpdate | TerrainModelConfigUpdate,
    opts?: { onSaved?: (view: ConfigView) => void },
  ) {
    setPhase("saving");
    const callbacks = {
      onSuccess: (view: ConfigView) => {
        opts?.onSaved?.(view);
        const cfg = kind === "judge" ? view.judge : view.terrain;
        // Nothing to verify when the save left the model unconfigured
        // (e.g. the key was cleared).
        if (!cfg.configured) {
          setPhase("idle");
          return;
        }
        testOnly();
      },
      onError: () => setPhase("save_error"),
    };
    // TerrainModelConfigUpdate is a structural subset of ModelConfigUpdate, so
    // the union is assignable to either mutation's variables.
    if (kind === "judge") saveJudge.mutate(update, callbacks);
    else saveTerrain.mutate(update, callbacks);
  }

  return {
    phase,
    /** Provider message from the last live test (e.g. a 401 body), if any. */
    message: test.data?.message ?? null,
    saveAndTest,
    testOnly,
  };
}

/** Connect/disconnect the XORCISE remote catalog (the Settings switch). */
export function useSetCatalogConnected() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (connected: boolean) =>
      api.put<ConfigView>("/config/catalog", { connected }),
    onSuccess: (view) => {
      qc.setQueryData(["config"], view);
      // The catalog list + status both change with the switch — refetch them.
      qc.invalidateQueries({ queryKey: ["config"] });
      qc.invalidateQueries({ queryKey: ["system"] });
      qc.invalidateQueries({ queryKey: ["missions"] });
    },
  });
}

/** Set the distributed-mode network addresses (applies on next server start). */
export function useSetNetwork() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (update: NetworkConfigUpdate) =>
      api.put<ConfigView>("/config/network", update),
    onSuccess: (view) => {
      qc.setQueryData(["config"], view);
      qc.invalidateQueries({ queryKey: ["config"] });
    },
  });
}
