"use client";

import { useState, type ReactNode } from "react";
import { CircleHelp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { cn } from "@/components/ui/cn";
import type { RunScore } from "@/lib/api/types";
import { PLOT_MARK } from "./agreement-plot";
import { hatch, KIND_META, type RunKind } from "./run-glyph";
import { CellFace, cellLook } from "./run-ledger";
import { StripColumn } from "./run-strip";

/** Which chart a key explains. Each view shows only its own shapes. */
export type KeyView = "strip" | "plot" | "ledger";

/** A sample run for the key. Never a real run: it only carries the scores its shape needs. */
function sample(over: Partial<RunScore>): RunScore {
  return {
    run_id: "key",
    agent_id: "",
    mission: "",
    mission_version: "",
    current_version: true,
    created_at: "",
    completed_at: null,
    outcome: "completed",
    terminal_trigger: "done",
    bucket: "scored",
    deterministic: 0.9,
    judge: 0.7,
    judge_upper: null,
    judge_status: "ok",
    overall: 0.8,
    overall_upper: null,
    judge_model: null,
    timed_out: false,
    intel_disclosed: 0,
    ...over,
  };
}

const SAMPLES: Record<RunKind, RunScore> = {
  scored: sample({}),
  timed_out: sample({ deterministic: 0.6, judge: 0.5, overall: 0.55, timed_out: true, outcome: "partial", terminal_trigger: "timeout" }),
  partial_judge: sample({ judge: 0.35, judge_upper: 0.85, judge_status: "partial", overall: 0.63 }),
  no_judge: sample({ deterministic: 1, judge: 0, judge_status: "unavailable", bucket: "no_judge", overall: 0.5 }),
  stopped: sample({ deterministic: 0.6, judge: 0.5, bucket: "stopped", outcome: "partial", terminal_trigger: "operator" }),
  infra_failed: sample({ deterministic: null, judge: null, judge_status: null, overall: null, bucket: "infra_failed", outcome: "infra_failed" }),
  grading: sample({ deterministic: null, judge: null, judge_status: null, overall: null, bucket: "grading" }),
};

/** The kinds each view draws, in the key's two groups: in the median, then left out. Grading is
 *  neither yet, and closes the second group. The plot places no run it has no scores for. */
const GROUPS: Record<KeyView, { in: RunKind[]; out: RunKind[] }> = {
  strip: {
    in: ["scored", "timed_out", "partial_judge"],
    out: ["no_judge", "stopped", "infra_failed", "grading"],
  },
  plot: { in: ["scored", "timed_out", "partial_judge"], out: ["no_judge", "stopped"] },
  ledger: {
    in: ["scored", "timed_out", "partial_judge"],
    out: ["no_judge", "stopped", "infra_failed", "grading"],
  },
};

/** One kind of run, drawn by the view's own renderer at key size (`big` for the help dialog). */
function Swatch({ view, kind, big = false }: { view: KeyView; kind: RunKind; big?: boolean }) {
  const run = SAMPLES[kind];
  if (view === "strip") {
    return (
      <span aria-hidden className={cn("flex", big ? "h-24 w-5" : "h-6 w-2.5")}>
        <StripColumn run={run} small={!big} />
      </span>
    );
  }
  if (view === "ledger") {
    return (
      <span aria-hidden className={cn("flex", big ? "size-10" : "size-3.5")}>
        <span className={cn("size-full", cellLook(run))}>
          <CellFace run={run} />
        </span>
      </span>
    );
  }
  const size = big ? "size-4" : "size-2.5";
  if (kind === "partial_judge") {
    return (
      <span aria-hidden className={cn("flex flex-col items-center", big ? "h-12" : "h-5")}>
        <span
          className={cn("w-1 flex-1", PLOT_MARK.judgeRange)}
          style={{ background: hatch("var(--color-chart-4)") }}
        />
        <span className={cn("shrink-0", size, PLOT_MARK.scored)} />
      </span>
    );
  }
  const look =
    kind === "stopped"
      ? PLOT_MARK.stopped
      : kind === "timed_out"
        ? PLOT_MARK.timed_out
        : kind === "no_judge"
          ? PLOT_MARK.no_judge
          : PLOT_MARK.scored;
  return <span aria-hidden className={cn("shrink-0", size, look)} />;
}

/**
 * The view's key, one line above its charts: what counts in the median, a divider, then what is
 * left out, each kind drawn by the view's own renderer. `active` outlines the kind of the run under
 * the pointer or focus, so the key teaches itself. One per view, not per card: every card on a
 * page draws the same way. "How to read this" opens the full explanation.
 */
export function RunKey({
  view,
  active,
  bare = false,
  className,
}: {
  view: KeyView;
  /** The kind of the run being inspected, outlined in the key. */
  active?: RunKind | null;
  /** Inside a card already: drop the strip's own frame. */
  bare?: boolean;
  className?: string;
}) {
  const groups = GROUPS[view];
  const item = (kind: RunKind) => (
    <li
      key={kind}
      data-kind={kind}
      data-active={active === kind || undefined}
      className={cn(
        "flex items-end gap-1.5 rounded-md border px-1.5 py-1 leading-4",
        active === kind ? "border-heading/70 bg-heading/5" : "border-transparent",
      )}
    >
      <Swatch view={view} kind={kind} />
      <span className="text-caption text-foreground">{KIND_META[kind].label}</span>
    </li>
  );
  return (
    <div
      className={cn(
        "flex flex-wrap items-end gap-x-5 gap-y-2",
        !bare && "rounded-md border border-border bg-card px-3 py-2",
        className,
      )}
    >
      {/* Headings and labels share one line and sit on the items' bottom edge, so "In the median"
          reads on the same line as "scored". */}
      <div className="flex flex-wrap items-end gap-1">
        <span className="pb-[5px] pr-1 text-label uppercase leading-4 text-primary">
          In the median
        </span>
        <ul aria-label="In the median" className="flex flex-wrap items-end gap-1">
          {groups.in.map(item)}
        </ul>
      </div>
      {/* The divider runs between the groups: across, once they stack on a narrow screen. */}
      <div className="flex w-full flex-wrap items-end gap-1 border-t border-border-hover pt-2 sm:w-auto sm:self-stretch sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0">
        <span className="pb-[5px] pr-1 text-label uppercase leading-4 text-text-tertiary">
          Left out
        </span>
        <ul aria-label="Left out of the median" className="flex flex-wrap items-end gap-1">
          {groups.out.map(item)}
        </ul>
      </div>
      <HowToRead view={view} className="ml-auto self-center" />
    </div>
  );
}

const READING: Record<KeyView, { lead: string; extra: ReactNode }> = {
  strip: {
    lead: "One column per run, oldest to newest; the card shows as many recent runs as fit. A column’s height is the run’s overall score: the lower part is what the deterministic checks earned, the upper part what the judge earned. Each carries half of the score.",
    extra: (
      <p>
        The dashed line is the <strong className="text-heading">median</strong> of the runs that
        count, over all of the agent’s runs, not only the ones that fit. Point at a column, or Tab
        to the strip and use the arrow keys, to see a run in words; Enter opens it.
      </p>
    ),
  },
  plot: {
    lead: "One point per run on this mission version: across is what the deterministic checks gave it, up is what the judge gave it. A version has its own card because a new version can change both.",
    extra: (
      <>
        <p>
          The diagonals join runs with the same <strong className="text-heading">overall</strong>{" "}
          score: 25, 50 and 75. Upper left, the judge rated the run above its checks; lower right,
          the checks passed what the judge marked down.
        </p>
        <p>
          A run with no judge has no place on the judge axis, so it sits on the strip under the
          plot at its checks score. Environment failures and runs still grading have no scores to
          place; they are counted under the plot. Point at a run, or pick an agent’s chip, to see
          whose it is.
        </p>
      </>
    ),
  },
  ledger: {
    lead: "One row per mission version, one cell per run, oldest to newest. The lower half of a cell is the deterministic checks’ score and the upper half the judge’s, brighter for higher. A row marked older is a version no longer installed.",
    extra: (
      <p>
        Each cell opens its run. Tab to the ledger, then use the arrow keys to move between runs,
        and Home or End to jump to a row’s oldest or newest.
      </p>
    ),
  },
};

const TITLE: Record<KeyView, string> = {
  strip: "How to read the run strip",
  plot: "How to read the agreement plot",
  ledger: "How to read the run ledger",
};

/** The "How to read this" help: the app's Dialog (focus held inside, Escape or the scrim closes
 *  it, focus returns to the trigger), holding the view's anatomy: each kind of run drawn large
 *  with what it means, grouped as the key groups them, then how to read the view as a whole. */
function HowToRead({ view, className }: { view: KeyView; className?: string }) {
  const [open, setOpen] = useState(false);
  const reading = READING[view];
  const group = (title: string, tone: string, kinds: RunKind[]) => (
    <section className="space-y-3">
      <h3 className={cn("text-label uppercase", tone)}>{title}</h3>
      <ul className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
        {kinds.map((kind) => (
          // Each drawing centred on its own text: a small plot mark or ledger cell sat at the
          // foot of a column-height slot, level with the explanation's last line.
          <li key={kind} className="flex items-center gap-3">
            <span
              className={cn(
                "flex w-10 shrink-0 items-center justify-center",
                view === "strip" && "h-24",
              )}
            >
              <Swatch view={view} kind={kind} big />
            </span>
            <span className="space-y-1">
              <span className="block text-caption font-bold text-heading">
                {KIND_META[kind].title}
              </span>
              <span className="block text-caption text-text-secondary">
                {KIND_META[kind].body}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
  return (
    <div className={className}>
      <button
        type="button"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
        className="flex items-center gap-1 rounded-sm text-caption text-text-secondary transition-colors hover:text-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <CircleHelp className="size-3.5" aria-hidden />
        How to read this
      </button>
      <Dialog open={open} onClose={() => setOpen(false)} title={TITLE[view]} size="lg">
        {/* Scrolls inside the dialog on a short screen, so the close button stays in reach. */}
        <div className="max-h-[65vh] space-y-5 overflow-y-auto pr-1">
          <p className="text-body text-text-secondary">{reading.lead}</p>
          {group("In the median", "text-primary", GROUPS[view].in)}
          <div className="border-t border-border" />
          {group("Left out", "text-text-tertiary", GROUPS[view].out)}
          <div className="space-y-2 border-t border-border pt-4 text-body text-text-secondary">
            {reading.extra}
          </div>
        </div>
        <div className="mt-4 flex justify-end">
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Done
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
