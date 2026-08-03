"use client";

import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import { useToastStore, type Toast } from "@/stores/toasts";
import { useUiStore } from "@/stores/ui";
import { STALL_TICK_MS, isStalled, secondsSince, useNow } from "./stall";
import type { RunEntry } from "@/lib/api/types";

/** Background poll cadence for run-transition notifications. The watcher shares
 *  the ["runs"] cache with useRuns/useRun, so a page-level 2s poll (live view)
 *  and mutation invalidations feed it too — 5s is only the floor. */
const NOTIFY_POLL_MS = 5000;

/** Toast content for a run that just reached a terminal state. */
function transitionToast(run: RunEntry): Omit<Toast, "id"> {
  const resultHref = `/runs/result?id=${encodeURIComponent(run.run_id)}`;
  const liveHref = `/runs/live?id=${encodeURIComponent(run.run_id)}`;
  switch (run.terminal_trigger) {
    case "done":
    case "completed":
      return {
        tone: "ok",
        title: `Run completed — ${run.name}`,
        href: resultHref,
        hrefLabel: "View result",
      };
    case "timeout":
      return {
        tone: "warn",
        title: `Run timed out — ${run.name}`,
        href: resultHref,
        hrefLabel: "View partial result",
      };
    case "budget":
      return {
        tone: "warn",
        title: `Run hit its budget — ${run.name}`,
        href: resultHref,
        hrefLabel: "View result",
      };
    case "error":
      return {
        tone: "err",
        title: `Run failed — ${run.name}`,
        href: liveHref,
        hrefLabel: "Inspect run",
      };
    default:
      // operator termination / unknown trigger
      return { tone: "info", title: `Run terminated — ${run.name}` };
  }
}

/** Invisible app-wide watcher (mounted in AppShell): polls the shared ["runs"]
 *  query and toasts when a run transitions to terminal, so a run finishing
 *  while the operator is on another page is never silently missed. */
export function RunNotificationsWatcher() {
  const push = useToastStore((s) => s.push);
  // null until the first snapshot lands — the first snapshot is baseline-only,
  // so a page load full of already-terminal runs doesn't produce a toast storm.
  const seen = useRef<Map<string, string> | null>(null);
  const mountedAt = useRef(Date.now());

  const { data } = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.get<RunEntry[]>("/runs"),
    refetchInterval: NOTIFY_POLL_MS,
  });

  // Agent-inactivity stalls (global scope): warn from ANY page when a live run that HAS been
  // exporting goes silent past the operator's threshold. `last_telemetry_at` is the server's
  // receipt clock compared against this browser's clock — skew only shifts WHEN the toast
  // fires, never whether telemetry is flowing. One toast per stall episode: the map records
  // the telemetry mark each warning fired for, so the same silence never re-toasts, while a
  // resume-then-restall (a new mark) warns again. Unlike terminal toasts there is no baseline
  // suppression — a run found already-stalled at mount is exactly what needs surfacing.
  const threshold = useUiStore((s) => s.stallThresholdSeconds);
  const now = useNow(STALL_TICK_MS);
  const stallToasted = useRef(new Map<string, string>());
  useEffect(() => {
    if (!data) return;
    for (const run of data) {
      if (run.state === "terminal" || !run.last_telemetry_at) continue;
      const staleSeconds = secondsSince(Date.parse(run.last_telemetry_at), now);
      if (!isStalled(staleSeconds, threshold)) continue;
      if (stallToasted.current.get(run.run_id) === run.last_telemetry_at) continue;
      stallToasted.current.set(run.run_id, run.last_telemetry_at);
      push({
        tone: "warn",
        title: `Agent inactive — ${run.name}`,
        body: `No telemetry for ${Math.max(1, Math.round((staleSeconds ?? 0) / 60))} min — the agent may have crashed or disconnected.`,
        href: `/runs/live?id=${encodeURIComponent(run.run_id)}`,
        hrefLabel: "Inspect run",
      });
    }
  }, [data, now, threshold, push]);

  useEffect(() => {
    if (!data) return;
    const prev = seen.current;
    const next = new Map<string, string>();
    for (const run of data) next.set(run.run_id, run.state);
    seen.current = next;
    if (prev === null) return; // baseline snapshot: record, never toast
    for (const run of data) {
      if (run.state !== "terminal") continue;
      const before = prev.get(run.run_id);
      // Transition = previously non-terminal, or a run first seen already
      // terminal that completed after mount (created + finished between polls).
      const completed = run.completed_at ? Date.parse(run.completed_at) : NaN;
      const transitioned =
        before !== undefined
          ? before !== "terminal"
          : !Number.isNaN(completed) && completed >= mountedAt.current;
      if (transitioned) push(transitionToast(run));
    }
  }, [data, push]);

  return null;
}
