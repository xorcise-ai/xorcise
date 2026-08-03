import { useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, errorDetail } from "@/lib/api/client";
import type {
  CatalogEntry,
  CatalogStatus,
  MissionManifest,
  IngestJobView,
  IngestStarted,
  PullJobStarted,
  PullJobView,
} from "@/lib/api/types";

export function useMissions() {
  return useQuery({
    queryKey: ["missions"],
    queryFn: () => api.get<CatalogEntry[]>("/missions"),
  });
}

/** Reachability of the XORCISE remote catalog (connected / error / disconnected). */
export function useCatalogStatus() {
  return useQuery({
    queryKey: ["catalog", "status"],
    queryFn: () => api.get<CatalogStatus>("/catalog/status"),
  });
}

/** Single mission derived from the catalog (no GET /missions/{id}). */
export function useMission(id: string) {
  return useQuery({
    queryKey: ["missions"],
    queryFn: () => api.get<CatalogEntry[]>("/missions"),
    select: (items) => items.find((c) => c.mission_id === id) ?? null,
  });
}

const PULL_POLL_MS = 1000;

/** Live pull state for one mission, shared by every component that renders it. */
export interface PullMissionState {
  start: () => void;
  /** Ask the in-flight pull to stop (no-op if there's no live job yet). */
  cancel: () => void;
  isPulling: boolean;
  /** True once a live pulling job exists to cancel — gates the Cancel control's visibility. */
  canCancel: boolean;
  /** A cancel was requested and the worker is still unwinding (status is still `pulling`). */
  isCancelling: boolean;
  /** The pull was cancelled — terminal; the card offers Pull again. */
  isCancelled: boolean;
  isSuccess: boolean;
  isError: boolean;
  errorDetail: string | null;
  phase: string | null;
  percent: number | null;
  bytesCurrent: number;
  bytesTotal: number;
  etaSeconds: number | null;
}

/**
 * Job-based pull (replaces the old single long-blocking POST, which the browser network
 * stack killed on multi-minute image downloads): POST /missions/{id}/pull-jobs returns
 * 202 + job_id, then we poll GET /missions/pull-jobs/{job_id} until installed/error.
 *
 * The poll query is keyed by mission_id in the shared react-query cache, so the catalog
 * card and the detail page observe the SAME live job — both render progress and both
 * disable their Pull buttons (the server dedups too). The initial fetch resolves the
 * active job for the mission, so a page reload resumes an in-flight progress bar.
 */
export function usePullMission(missionId: string): PullMissionState {
  const qc = useQueryClient();
  const enc = encodeURIComponent(missionId);
  const queryKey = ["pull-job", missionId];

  const job = useQuery<PullJobView | null>({
    queryKey,
    queryFn: async () => {
      // Poll a known in-flight job by id (only that endpoint reports the terminal state);
      // otherwise look up the active job so a reload / second view joins an in-flight pull.
      const prev = qc.getQueryData<PullJobView | null>(queryKey);
      if (prev && prev.status === "pulling") {
        return api.get<PullJobView>(
          `/missions/pull-jobs/${encodeURIComponent(prev.job_id)}`,
        );
      }
      return api.get<PullJobView | null>(
        `/missions/pull-jobs?mission_id=${enc}`,
      );
    },
    enabled: !!missionId,
    refetchInterval: (q) =>
      q.state.data?.status === "pulling" ? PULL_POLL_MS : false,
  });

  const start = useMutation({
    mutationFn: () =>
      api.post<PullJobStarted>(`/missions/${enc}/pull-jobs`),
    // Clear any lingering TERMINAL view (a prior cancelled/errored/installed job) the instant a
    // re-pull begins, so during the start round-trip the card shows a clean "Pulling…" and not a
    // stale "Pull cancelled."/"Pull failed." caption alongside it — and so cancel() can't target
    // the old job's id before the new one is seeded.
    onMutate: () => {
      qc.setQueryData<PullJobView | null>(queryKey, null);
    },
    onSuccess: ({ job_id }) => {
      // Seed the shared poll with the new job so every observer starts polling immediately.
      const seeded: PullJobView = {
        job_id,
        mission_id: missionId,
        status: "pulling",
        phase: "resolving",
        bytes_current: 0,
        bytes_total: 0,
        percent: null,
        eta_seconds: null,
        detail: null,
        cancel_requested: false,
        entry: null,
      };
      qc.setQueryData<PullJobView | null>(queryKey, seeded);
      void qc.invalidateQueries({ queryKey });
    },
  });

  const view = job.data ?? null;
  const status = view?.status;

  // Cancel the in-flight pull. The server sets cancel_requested and its worker aborts before
  // install (recording `cancelled`); polling continues meanwhile, so we write the returned view
  // straight into the shared cache to reflect "Cancelling…" without waiting for the next tick.
  const cancel = useMutation({
    mutationFn: (jobId: string) =>
      api.post<PullJobView>(
        `/missions/pull-jobs/${encodeURIComponent(jobId)}/cancel`,
      ),
    onSuccess: (v) => {
      // Only reflect the cancel if it still concerns the job the cache currently holds — a slow
      // cancel response for an old job must never clobber a newer pull that has since started.
      const cur = qc.getQueryData<PullJobView | null>(queryKey);
      if (!cur || cur.job_id === v.job_id) {
        qc.setQueryData<PullJobView | null>(queryKey, v);
      }
    },
  });

  useEffect(() => {
    if (status === "installed") {
      void qc.invalidateQueries({ queryKey: ["missions"] });
    }
  }, [status, qc]);

  const cancelRequested = view?.cancel_requested ?? false;
  // Cancel only ever targets a LIVE pulling job (status stays 'pulling' until the worker
  // unwinds). This excludes the start round-trip (view not yet seeded) and any lingering
  // terminal job, so a click can only cancel the pull it visibly controls.
  const liveJobId = status === "pulling" ? view?.job_id : undefined;
  return {
    start: () => start.mutate(),
    cancel: () => {
      if (liveJobId) cancel.mutate(liveJobId);
    },
    isPulling: start.isPending || status === "pulling",
    canCancel: !!liveJobId,
    isCancelling: cancel.isPending || (cancelRequested && status === "pulling"),
    isCancelled: status === "cancelled",
    isSuccess: status === "installed",
    isError: start.isError || status === "error",
    // Two failure sources, both reported in the server's own words: a job that failed
    // server-side carries `view.detail`, while a rejected START request carries its reason
    // in the response body. `start.error.message` is wire trivia ("POST /… → 503"), so it
    // is never shown — the same rule the run form follows.
    errorDetail:
      status === "error"
        ? (view?.detail ?? null)
        : errorDetail(start.error),
    phase: view?.phase ?? null,
    percent: view?.percent ?? null,
    bytesCurrent: view?.bytes_current ?? 0,
    bytesTotal: view?.bytes_total ?? 0,
    etaSeconds: view?.eta_seconds ?? null,
  };
}

/** "12.4 MB", "1.2 GB" — decimal units, one decimal place. */
export function formatBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)} KB`;
  return `${Math.max(0, Math.round(n))} B`;
}

/** "0:42", "12:05" — m:ss from seconds. */
export function formatEta(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * Phase → what the operator is told. `preparing_image` and `extracting_image` exist because a
 * docker pull spends long stretches moving no bytes: negotiating, finding layers already in the
 * local store, and unpacking (minutes, on a multi-GB image, AFTER the last byte lands). All three
 * used to be labelled "Downloading image…" against a 0 B counter, which reads as a hung download —
 * an operator cancelled a healthy pull because of it.
 */
const PULL_PHASE_LABELS: Record<string, string> = {
  resolving: "Resolving…",
  preparing_image: "Preparing image…",
  pulling_image: "Downloading image…",
  extracting_image: "Extracting image…",
  downloading_bundle: "Downloading files…",
  installing: "Installing…",
  done: "Installed",
};

export function pullPhaseLabel(phase: string | null): string {
  return (phase && PULL_PHASE_LABELS[phase]) || "Pulling…";
}

// Uninstall an installed mission — local or pulled. Refreshes the catalog so it drops
// out of Your Own / falls back to not-installed in the library.
export function useDeleteMission() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.del(`/missions/${encodeURIComponent(id)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["missions"] }),
  });
}

/** The full mission.json for an id (installed copy, else the connected library). */
export function useMissionManifest(id: string) {
  return useQuery({
    queryKey: ["missions", id, "manifest"],
    queryFn: () =>
      api.get<MissionManifest>(
        `/missions/${encodeURIComponent(id)}/manifest`,
      ),
    enabled: !!id,
  });
}

const INGEST_POLL_MS = 1500;
const INGEST_POLL_CAP_MS = 15 * 60 * 1000; // a fused-image build can take many minutes

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Ingest a local bundle directory into Your Own (builds its fused image).
 *
 * The build runs async on the server: POST returns 202 + a job_id, then we poll
 * GET /missions/ingest/{job_id} until it reaches `installed` (resolve with the job) or `error`
 * (reject with the detail) — so a minutes-long build never hangs the request.
 */
export function useIngestMission() {
  const qc = useQueryClient();
  return useMutation<IngestJobView, Error, string>({
    mutationFn: async (bundleDir: string) => {
      const { job_id } = await api.post<IngestStarted>("/missions/ingest", {
        bundle_dir: bundleDir,
      });
      const deadline = Date.now() + INGEST_POLL_CAP_MS;
      for (;;) {
        const job = await api.get<IngestJobView>(
          `/missions/ingest/${encodeURIComponent(job_id)}`,
        );
        if (job.status === "installed") return job;
        if (job.status === "error") {
          throw new Error(job.detail ?? "ingest failed");
        }
        if (Date.now() >= deadline) throw new Error("ingest timed out");
        await sleep(INGEST_POLL_MS);
      }
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["missions"] }),
  });
}
