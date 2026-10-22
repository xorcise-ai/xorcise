"use client";

import { useState } from "react";
import { cn } from "@/components/ui/cn";
import type { RunScore } from "@/lib/api/types";
import { hatch, runKind, type RunKind } from "./run-glyph";
import { RunInspector } from "./run-inspector";

/** The plot's marks, shared with the key's examples. */
export const PLOT_MARK = {
  scored: "rounded-full bg-foreground",
  // Red means only this, as on the strip and the ledger: the agent did not finish.
  timed_out: "rounded-full bg-foreground ring-[1.5px] ring-err ring-offset-1 ring-offset-card",
  stopped: "rounded-full border-[1.5px] border-text-secondary",
  no_judge: "rounded-full border-[1.5px] border-dashed border-data bg-card",
  judgeRange: "opacity-60",
} as const;

/** Repeats at one position would hide each other; nudge each further one a few px aside. */
const NUDGE = [0, 5, -5, 10, -10, 15, -15];
const pctPos = (v: number | null | undefined) => `${(v ?? 0) * 100}%`;

/**
 * One mission version's runs, each placed by what the deterministic checks gave it (across) and
 * what the judge gave it (up). The diagonals join equal overall scores (25, 50, 75), so the plot
 * still answers "how well", and also "according to which grader": a run in the lower right
 * passed the checks and failed the judge. The checks and the rubric belong to the mission
 * version, which is why a plot never mixes versions.
 *
 * A run whose judge never ran has no place on the judge axis, so it sits on the strip under the
 * plot at its deterministic score. A judge that scored part of its rubric draws a hatched bar up
 * to its upper bound. A run that timed out is ringed in amber, at the score it earned; one an
 * operator stopped is a hollow ring, outside the median. Environment failures
 * and runs still grading have no scores to place and are only counted, by the caller.
 *
 * Pointing at a run reveals whose it is: a label beside it names the agent with its median and
 * judge gap on this version (`agentLabel`), and `onHoverAgent` lets the caller light up that
 * agent's other runs. `highlight` dims every agent's runs but one. Decorative to assistive
 * technology: `summary` describes the plot, and the caller's agent chips carry each agent's
 * numbers in words and are the keyboard and touch way to single one out.
 */
export function AgreementPlot({
  runs,
  agentName,
  agentLabel,
  highlight,
  onHoverAgent,
  onInspect,
  summary,
}: {
  runs: RunScore[];
  agentName: (id: string) => string;
  /** One line about an agent on this version, e.g. "recon · median 57% · judge 64 below checks". */
  agentLabel: (id: string) => string;
  highlight?: string | null;
  onHoverAgent?: (id: string | null) => void;
  /** The kind of the run under the pointer, for the page's key. */
  onInspect?: (kind: RunKind | null) => void;
  summary: string;
}) {
  const [hovered, setHovered] = useState<{ run: RunScore; onRail: boolean } | null>(null);
  const enter = (run: RunScore, onRail: boolean) => {
    setHovered({ run, onRail });
    onHoverAgent?.(run.agent_id);
    onInspect?.(runKind(run));
  };
  const leave = () => {
    setHovered(null);
    onHoverAgent?.(null);
    onInspect?.(null);
  };
  const placed = runs.filter((r) => r.bucket === "scored" || r.bucket === "stopped");
  const rail = runs.filter((r) => r.bucket === "no_judge");
  const seen = new Map<string, number>();
  const nudge = (key: string) => {
    const n = seen.get(key) ?? 0;
    seen.set(key, n + 1);
    return NUDGE[n % NUDGE.length];
  };
  const dim = (r: RunScore) => highlight != null && r.agent_id !== highlight;

  return (
    <figure className="m-0 mx-auto w-full max-w-sm" role="img" aria-label={summary}>
      <div className="flex gap-2" aria-hidden>
        <span className="flex w-3 items-center justify-center">
          <span className="-rotate-90 whitespace-nowrap text-label uppercase text-chart-4">
            Judge →
          </span>
        </span>
        <div className="min-w-0 flex-1 space-y-1">
          <div
            data-testid="agreement-plot"
            className="relative aspect-square w-full border-b border-l border-border-hover"
          >
            <svg
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              className="absolute inset-0 size-full"
            >
              <line x1="0" y1="50" x2="50" y2="100" className="stroke-border" strokeWidth="0.5" />
              <line x1="0" y1="0" x2="100" y2="100" className="stroke-border-hover" strokeWidth="0.5" />
              <line x1="50" y1="0" x2="100" y2="50" className="stroke-border" strokeWidth="0.5" />
            </svg>
            <span className="absolute left-[51%] bottom-0.5 text-label text-text-tertiary">25</span>
            <span className="absolute right-0.5 bottom-0.5 text-label text-text-tertiary">50</span>
            <span className="absolute right-0.5 bottom-[51%] text-label text-text-tertiary">75</span>
            <span className="absolute left-1 top-1 text-label text-text-tertiary">judge favours</span>
            <span className="absolute bottom-4 right-1 text-label text-text-tertiary">checks favour</span>
            {placed.map((r) => {
              const dx = nudge(`${r.deterministic}:${r.judge}`);
              const partial = r.judge_status === "partial" && r.judge_upper != null;
              return (
                <div key={r.run_id} className={cn("transition-opacity", dim(r) && "opacity-20")}>
                  {partial && (
                    <div
                      data-part="judge-range"
                      className={cn("absolute w-1 -translate-x-1/2", PLOT_MARK.judgeRange)}
                      style={{
                        left: `calc(${pctPos(r.deterministic)} + ${dx}px)`,
                        bottom: pctPos(r.judge),
                        height: `${((r.judge_upper ?? 0) - (r.judge ?? 0)) * 100}%`,
                        background: hatch("var(--color-chart-4)"),
                      }}
                    />
                  )}
                  <div
                    data-testid="agreement-point"
                    data-bucket={r.bucket}
                    data-agent={agentName(r.agent_id)}
                    onMouseEnter={() => enter(r, false)}
                    onMouseLeave={leave}
                    className={cn(
                      "absolute size-2.5 -translate-x-1/2 translate-y-1/2",
                      r.bucket === "stopped"
                        ? PLOT_MARK.stopped
                        : r.timed_out
                          ? PLOT_MARK.timed_out
                          : PLOT_MARK.scored,
                    )}
                    style={{
                      left: `calc(${pctPos(r.deterministic)} + ${dx}px)`,
                      bottom: pctPos(r.judge),
                    }}
                  />
                </div>
              );
            })}
            {hovered && <HoverLabel {...hovered} agent={agentLabel(hovered.run.agent_id)} />}
          </div>
          {/* Points along the top, the label along the bottom, so a run at 0 never covers it. */}
          <div className="relative h-7 border-b border-dashed border-border">
            <span className="absolute bottom-0.5 left-0 text-label text-text-tertiary">
              no judge ↑
            </span>
            {rail.map((r) => (
              <div
                key={r.run_id}
                data-testid="agreement-rail"
                data-agent={agentName(r.agent_id)}
                onMouseEnter={() => enter(r, true)}
                onMouseLeave={leave}
                className={cn(
                  "absolute top-1 size-2.5 -translate-x-1/2",
                  PLOT_MARK.no_judge,
                  dim(r) && "opacity-20",
                )}
                style={{ left: `calc(${pctPos(r.deterministic)} + ${nudge(`rail:${r.deterministic}`)}px)` }}
              />
            ))}
          </div>
          <div className="flex justify-between text-label text-text-tertiary">
            <span>0</span>
            <span className="uppercase tracking-wider text-data">Deterministic →</span>
            <span>100</span>
          </div>
        </div>
      </div>
    </figure>
  );
}

/** The run under the pointer, in the inspector every view shares, headed by whose run it is. Kept
 *  inside the plot: a run near either edge anchors it on that side instead of centring it. */
function HoverLabel({ run, onRail, agent }: { run: RunScore; onRail: boolean; agent: string }) {
  const x = run.deterministic ?? 0;
  const shift = x < 0.25 ? "0%" : x > 0.75 ? "-100%" : "-50%";
  // Open towards the side with more room: below a run in the upper half, above one in the lower.
  const below = !onRail && (run.judge ?? 0) >= 0.5;
  return (
    <div
      data-testid="agreement-hover"
      className="pointer-events-none absolute z-20"
      style={{
        left: pctPos(x),
        bottom: onRail ? "0%" : pctPos(run.judge),
        transform: `translate(${shift}, ${below ? "calc(100% + 12px)" : "-12px"})`,
      }}
    >
      <RunInspector run={run} agent={agent} />
    </div>
  );
}
