"use client";

import { Bot, Boxes, Flag } from "lucide-react";
import { Chip, ChipRow } from "@/components/ui/chip";
import type { DotTone } from "@/components/ui/dot";
import type { RunEntry, RunEnvironment } from "@/lib/api/types";
import { runPhase, toneColor, type RunPhase } from "./run-phase";

/**
 * The "glowing bulb" — a filled dot whose colour + glow reflect what the backend
 * is doing (awaiting agent / streaming / solved / failed). Pulses while active.
 * Sits beside a short label + help text so the operator always knows the live state.
 */
export function BackendStatusBulb({
  phase,
  compact = false,
}: {
  phase: RunPhase;
  /** Inline dot + label (help text demoted to a tooltip) for the merged run-page header. */
  compact?: boolean;
}) {
  const color = toneColor(phase.tone);
  if (compact) {
    return (
      <span className="inline-flex items-center gap-2" title={phase.helpText}>
        <span
          className={`inline-block size-2.5 shrink-0 rounded-full ${phase.active ? "motion-safe:animate-pulse" : ""}`}
          style={{
            backgroundColor: color,
            boxShadow: `0 0 ${phase.active ? "8px 2px" : "4px 1px"} ${color}`,
          }}
          aria-hidden
        />
        <span className="text-dense font-semibold" style={{ color }}>
          {phase.label}
        </span>
      </span>
    );
  }
  return (
    <div className="flex items-start gap-3">
      <span
        className={`mt-1 inline-block size-3 shrink-0 rounded-full ${phase.active ? "motion-safe:animate-pulse" : ""}`}
        style={{
          backgroundColor: color,
          // The glow — a soft halo in the tone colour; stronger while active.
          boxShadow: `0 0 ${phase.active ? "10px 3px" : "5px 1px"} ${color}`,
        }}
        aria-hidden
      />
      <div className="min-w-0">
        <p className="text-body font-semibold" style={{ color }}>
          {phase.label}
        </p>
        <p className="mt-2 text-dense text-text-secondary">{phase.helpText}</p>
      </div>
    </div>
  );
}

export interface ConnectionChipData {
  icon: typeof Bot;
  label: string;
  value: string;
  tone: "green" | "amber" | "red" | "muted";
  title?: string;
}

/**
 * The connection statuses as plain data — shared between the ConnectionPanel chips and the
 * run-page header's compact meta cells.
 *
 * Every label here has exactly ONE referent (the run-page header carries `Harness` for the
 * agent's identity, so a chip may never be called "Agent" — the same word for two facts made
 * the header unreadable). The agent's LINK state is not a chip at all: the header's status
 * bulb already renders it as "Awaiting agent" / "Streaming" / "Complete", so restating it here
 * only bought a duplicate cell. `Objective` is terminal-only — while the run is live the
 * status badge already says so, and an always-on "In Progress" cell was the one that orphaned
 * itself onto a half-empty second row.
 */
/** Environment lifecycle chip, from the state the SERVER observes (GET /runs/{id}/environment).
 *
 *  This used to be inferred from `sandbox_ref` alone, which could only ever say "Starting" or
 *  "Ready" and was wrong twice over: a STATIC mission has no image, so it read as a perpetual
 *  "Starting" for something that was never coming; and an environment that had DIED still read as
 *  "Ready", giving the operator no signal while their agent worked a dead target. `detail` rides
 *  along as the tooltip so "Starting" says what it is waiting for. */
export function environmentChip(env: RunEnvironment | undefined): ConnectionChipData {
  const byState: Record<
    RunEnvironment["state"],
    { value: string; tone: "green" | "amber" | "red" | "muted" }
  > = {
    none: { value: "None", tone: "muted" },
    starting: { value: "Starting", tone: "amber" },
    ready: { value: "Ready", tone: "green" },
    failed: { value: "Failed", tone: "red" },
    released: { value: "Released", tone: "muted" },
  };
  // Not loaded yet → the honest "still coming up", never an invented ready/failed verdict.
  const { value, tone } = byState[env?.state ?? "starting"];
  return {
    icon: Boxes,
    label: "Environment",
    value,
    tone,
    title: env?.detail || undefined,
  };
}

export function connectionChips(
  run: RunEntry,
  eventCount: number,
  environment?: RunEnvironment,
): ConnectionChipData[] {
  const phase = runPhase(run, eventCount, environment?.state);
  const terminal = run.state === "terminal";
  const chips: ConnectionChipData[] = [environmentChip(environment)];
  if (terminal) {
    const solved = phase.key === "solved";
    chips.push({
      icon: Flag,
      label: "Objective",
      value: solved ? "Solved" : "Not Solved",
      tone: solved ? "green" : "red",
    });
  }
  return chips;
}

/** The run-phase tone vocabulary → the design system's dot tones.
 *
 *  `amber` maps to `primary`, not `warn`: `toneColor()` (run-phase.ts) already resolves amber to
 *  --color-primary for the status bulb, and the bulb and the chip report the SAME phase — a chip
 *  dot in --color-warning beside a bulb in --color-primary would read as two different states.
 *  Moving both to the warning rung is a run-phase change, not a presentation one. */
export const CHIP_DOT_TONE: Record<ConnectionChipData["tone"], DotTone> = {
  green: "ok",
  amber: "primary",
  red: "err",
  muted: "muted",
};

/** Environment / Objective connection chips derived from the run + trace. Content-sized and
 *  wrapping, so a variable-length chip list can never orphan a cell on a half-empty row. */
export function ConnectionPanel({
  run,
  eventCount,
  environment,
}: {
  run: RunEntry;
  eventCount: number;
  environment?: RunEnvironment;
}) {
  return (
    // The design system's Chip (KEY · dot · value). Its leading slot takes an icon OR the dot,
    // never both, and for these chips the DOT is the payload — it is the only thing that says
    // Ready vs Failed vs Released — so the decorative Boxes/Flag mark yields to it.
    <ChipRow>
      {connectionChips(run, eventCount, environment).map((c) => (
        <Chip
          key={c.label}
          label={c.label}
          value={c.value}
          tone={CHIP_DOT_TONE[c.tone]}
          title={c.title}
        />
      ))}
    </ChipRow>
  );
}
