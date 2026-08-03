import type { RunEntry, RunEnvironment } from "@/lib/api/types";

export type Tone = "amber" | "green" | "red" | "muted";

export interface RunPhase {
  key:
    | "provisioning"
    | "environment-failed"
    | "awaiting-agent"
    | "connecting"
    | "streaming"
    | "stalled"
    | "solved"
    | "failed"
    | "ended";
  label: string; // short status word for the bulb
  helpText: string; // one-line explanation of what the backend is doing/waiting on
  tone: Tone;
  /** Pulse + glow while something is actively in flight. */
  active: boolean;
}

/**
 * Derive the run's lifecycle phase from its state, terminal trigger, and whether
 * any trace has arrived. Pure so the bulb + guidance + connection panel all read
 * the same source of truth (and it's unit-testable).
 *
 * Server states: "created" → "active" → "terminal" (trigger says how it ended).
 */
export function runPhase(
  run: Pick<RunEntry, "state" | "terminal_trigger">,
  eventCount: number,
  /** The mission environment's observed state, when known. Without it the phase falls back to
   *  the agent-only reading, so callers that don't poll the environment are unaffected. */
  environmentState?: RunEnvironment["state"],
  /** Seconds the run has been silent past the operator's inactivity threshold — null/omitted
   *  when the run is not stalled. The CALLER owns the threshold comparison; this stays a pure
   *  presentation of an established fact. */
  stalledSeconds?: number | null,
): RunPhase {
  if (run.state === "terminal") {
    const t = run.terminal_trigger;
    if (t === "done" || t === "completed")
      return {
        key: "solved",
        label: "Complete",
        helpText: "The run finished and a result was recorded.",
        tone: "green",
        active: false,
      };
    if (t === "error" || t === "timeout" || t === "budget")
      return {
        key: "failed",
        label: t,
        helpText:
          t === "error"
            ? "The run ended on an error."
            : `The run hit its ${t} limit before finishing.`,
        tone: "red",
        active: false,
      };
    return {
      key: "ended",
      label: t ?? "terminal",
      helpText: "The run is no longer active.",
      tone: "muted",
      active: false,
    };
  }

  // A dead environment outranks anything the agent is doing: the run is about to be closed out,
  // and reporting "Streaming" because the agent is still emitting into the void would hide the
  // actual fault.
  if (environmentState === "failed")
    return {
      key: "environment-failed",
      label: "Environment failed",
      helpText: "The mission environment exited — this run is being closed out.",
      tone: "red",
      active: false,
    };

  // Silence past the operator's threshold outranks "Streaming": a green pulse saying "watch it
  // work" while nothing has arrived for minutes is exactly the misread this phase exists to fix.
  if (eventCount > 0 && stalledSeconds != null)
    return {
      key: "stalled",
      label: "Stalled",
      helpText: `No telemetry for ${Math.max(1, Math.round(stalledSeconds / 60))} min — the agent may have crashed or disconnected. The run keeps counting against its budget until it times out.`,
      tone: "red",
      active: false,
    };

  if (eventCount > 0)
    return {
      key: "streaming",
      label: "Streaming",
      helpText: "Your agent is connected and emitting traces — watch it work below.",
      tone: "green",
      active: true,
    };

  // No trace yet, and the environment is still coming up. Saying "Awaiting agent" here reads as
  // "your agent is late" and invites launching it against targets that do not exist yet — the way
  // an agent ends up joined to the tailnet with nothing to reach. Name what is actually pending.
  // (A STATIC mission reports "none": there is no environment to wait for, so the agent really
  // is the next step and the awaiting-agent branch below is correct.)
  if (environmentState === "starting")
    return {
      key: "provisioning",
      label: "Starting environment",
      helpText: "The mission environment is still coming up — wait for it to be ready before launching your agent.",
      tone: "amber",
      active: true,
    };

  // No trace yet — either freshly created (awaiting the paste) or active but quiet.
  return {
    key: run.state === "active" ? "connecting" : "awaiting-agent",
    label: run.state === "active" ? "Connecting" : "Awaiting agent",
    helpText: "No trace received yet. Make sure your agent is emitting OpenTelemetry traces so XORCISE can capture them.",
    tone: "amber",
    active: true,
  };
}

/** CSS custom-property color for a tone (used for the bulb glow + accents). */
export function toneColor(tone: Tone): string {
  switch (tone) {
    case "green":
      return "var(--color-ok)";
    case "red":
      return "var(--color-err)";
    case "amber":
      return "var(--color-primary)";
    case "muted":
      return "var(--color-muted-foreground)";
  }
}
