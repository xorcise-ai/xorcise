"use client";

import { useEffect, useState } from "react";

/** How often staleness re-evaluates. Coarse on purpose — the warning is minutes-scale, and a
 *  stalled run's query data never changes, so polling alone can't re-run the check. */
export const STALL_TICK_MS = 15_000;

/** A ticking clock: re-renders the consumer every `intervalMs` with a fresh Date.now(). */
export function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/** Whole seconds since `lastMs`, or null when there is no moment to measure from. */
export function secondsSince(lastMs: number | null, nowMs: number): number | null {
  if (lastMs == null || Number.isNaN(lastMs)) return null;
  return Math.max(0, Math.floor((nowMs - lastMs) / 1000));
}

/** True once the silence has run past the operator's threshold. */
export function isStalled(
  staleSeconds: number | null,
  thresholdSeconds: number,
): boolean {
  return staleSeconds != null && staleSeconds >= thresholdSeconds;
}
