import type { RunEntry } from "@/lib/api/types";

export type RunBadge = "default" | "ok" | "err" | "info" | "muted";

/**
 * Map a run's state (+ terminal trigger) to a label + badge variant.
 * Server states: "created" -> "active" -> "terminal"; the trigger
 * (done/completed/error/timeout/budget) describes how a terminal run ended.
 */
export function runStateMeta(
  state: string,
  trigger?: string | null,
): { label: string; variant: RunBadge } {
  if (state === "created") return { label: "created", variant: "default" };
  if (state === "active") return { label: "running", variant: "default" };
  if (state === "terminal") {
    if (trigger === "done" || trigger === "completed")
      return { label: "completed", variant: "ok" };
    if (
      trigger === "error" ||
      trigger === "timeout" ||
      trigger === "budget" ||
      trigger === "deploy_failed"
    )
      return { label: trigger, variant: "err" };
    return { label: "terminal", variant: "muted" };
  }
  return { label: state, variant: "muted" };
}

export function isTerminal(run: Pick<RunEntry, "state">): boolean {
  return run.state === "terminal";
}

/** Status tone for the shared §10 palette: amber (running/attention), green (success),
 *  red (failure), muted (grey — terminal/cancelled). Mirrors the live-run tone system. */
export type RunTone = "amber" | "green" | "red" | "muted";

/** Where a run's primary action navigates. */
export type RunActionTarget = "live" | "result";

export interface RunPresentation {
  /** Human status word from the §10 vocabulary. */
  label: string;
  tone: RunTone;
  /** State-appropriate primary action (label + destination). */
  action: { label: string; target: RunActionTarget };
}

/**
 * Richer per-card status + state-appropriate action for the Run History cards (report §10).
 *
 * Deliberately separate from {@link runStateMeta} so the live-run, dashboard, and results
 * consumers (and their locked tests) stay untouched: this maps the same server state/trigger onto
 * the §10 status vocabulary (Running / Completed / Partial / Timeout / Failed / Terminal) and the
 * matching action (Running → Open Live Run; Completed/Partial → View Result; Timeout → View
 * Partial Result; Failed → Inspect Run). The `budget` trigger reads as "Partial" — a run that hit
 * its budget stops with partial progress rather than erroring or timing out hard.
 */
export function runPresentation(
  state: string,
  trigger?: string | null,
): RunPresentation {
  const openLive = { label: "Open Live Run", target: "live" as const };
  if (state === "created")
    return { label: "Created", tone: "amber", action: openLive };
  if (state === "active")
    return { label: "Running", tone: "amber", action: openLive };
  if (state === "terminal") {
    if (trigger === "done" || trigger === "completed")
      return {
        label: "Completed",
        tone: "green",
        action: { label: "View Result", target: "result" },
      };
    if (trigger === "timeout")
      return {
        label: "Timeout",
        tone: "red",
        action: { label: "View Partial Result", target: "result" },
      };
    if (trigger === "budget")
      return {
        label: "Partial",
        tone: "amber",
        action: { label: "View Result", target: "result" },
      };
    if (trigger === "error")
      return {
        label: "Failed",
        tone: "red",
        action: { label: "Inspect Run", target: "live" },
      };
    // The readiness gate closed this run out: its environment died at deploy or never came up. A
    // failure the operator must see — and there is nothing graded to open, so inspect the run.
    if (trigger === "deploy_failed")
      return {
        label: "Deploy failed",
        tone: "red",
        action: { label: "Inspect Run", target: "live" },
      };
    // terminated / cancelled / unknown trigger → grey. Inspect via the live view, which always
    // renders for any run (a terminated run may not have a graded result to open).
    return {
      label: "Terminal",
      tone: "muted",
      action: { label: "Inspect Run", target: "live" },
    };
  }
  return { label: state, tone: "muted", action: openLive };
}
