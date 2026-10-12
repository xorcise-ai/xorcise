import type { RunEntry, RunStatus } from "@/lib/api/types";

// How a terminal run counts in the aggregates — the server's answer (RunEntry.outcome, derived from
// the trigger by contracts/run.py), never re-derived here. Shared by every aggregate (Results
// agents + missions, the Agents list, the dashboard). Four private copies of the trigger rule once
// listed a "budget" trigger nothing writes and missed "operator" (#159).
export interface RunCounting {
  /** Did not end on the agent's own terms — excluded from Average / Best. */
  partial: boolean;
  /** Finished on the agent's own terms. */
  completed: boolean;
  /** Cut short by an environment failure: never graded, and not one of the agent's attempts,
   *  so it is left out of every score AND rate and disclosed as its own count (#109). */
  infraFailed: boolean;
}

/** Spread the server's `outcome` into the flags the aggregates fold. A run with no outcome (not
 *  ended, or an unknown trigger) is none of the three. */
export function classifyRun(outcome: RunEntry["outcome"] | undefined): RunCounting {
  return {
    partial: outcome === "partial",
    completed: outcome === "completed",
    infraFailed: outcome === "infra_failed",
  };
}

export function isTerminal(run: Pick<RunEntry, "state">): boolean {
  return run.state === "terminal";
}

/** Status tone for the shared §10 palette: amber (running/attention), green (success),
 *  red (failure), muted (grey — terminal/cancelled). Mirrors the live-run tone system. */
export type RunTone = "amber" | "green" | "red" | "muted";

/** The server's status tone (contracts.run.RunTone) → the GUI palette. */
const TONE: Record<RunStatus["tone"], RunTone> = {
  ok: "green",
  active: "amber",
  err: "red",
  muted: "muted",
};

export function runTone(status: RunStatus): RunTone {
  return TONE[status.tone];
}

/** Where a run's primary action navigates. */
export type RunActionTarget = "live" | "result";

export interface RunAction {
  label: string;
  target: RunActionTarget;
}

/** The state-appropriate primary action: a live run opens live; a graded run opens its result
 *  (a partial one says so); anything else — an environment failure, an unknown ending — has no
 *  result to open, so it sends the operator to inspect the run. */
export function runAction(run: Pick<RunEntry, "state" | "outcome">): RunAction {
  if (run.state === "created" || run.state === "active")
    return { label: "Open live run", target: "live" };
  if (run.outcome === "completed") return { label: "View result", target: "result" };
  if (run.outcome === "partial")
    return { label: "View partial result", target: "result" };
  return { label: "Inspect run", target: "live" };
}

export interface RunPresentation {
  /** The server's status word (RunEntry.status.label). */
  label: string;
  tone: RunTone;
  /** State-appropriate primary action (label + destination). */
  action: RunAction;
}

/**
 * Status + action for every run surface (cards, lists, the run page, the dashboard). The label
 * and tone are the server's (`RunEntry.status`, contracts.run.run_status) — the same wording
 * `xorcise run list` prints — so the GUI holds no trigger table of its own and cannot describe a
 * run differently from the CLI (#165).
 */
export function runPresentation(
  run: Pick<RunEntry, "state" | "status" | "outcome">,
): RunPresentation {
  return { label: run.status.label, tone: runTone(run.status), action: runAction(run) };
}
