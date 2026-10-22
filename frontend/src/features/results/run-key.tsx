"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { CircleHelp, X } from "lucide-react";
import { cn } from "@/components/ui/cn";
import type { RunScore } from "@/lib/api/types";
import { PLOT_MARK } from "./agreement-plot";
import { hatch } from "./run-glyph";
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
    ...over,
  };
}

const SAMPLES = {
  scored: sample({}),
  partial: sample({ judge: 0.35, judge_upper: 0.85, judge_status: "partial", overall: 0.63 }),
  no_judge: sample({ deterministic: 1, judge: 0, judge_status: "unavailable", bucket: "no_judge", overall: 0.5 }),
  ended_early: sample({ deterministic: 0.6, judge: 0.4, bucket: "ended_early", outcome: "partial", terminal_trigger: "timeout" }),
  infra_failed: sample({ deterministic: null, judge: null, judge_status: null, overall: null, bucket: "infra_failed", outcome: "infra_failed" }),
  grading: sample({ deterministic: null, judge: null, judge_status: null, overall: null, bucket: "grading" }),
};

type Kind = keyof typeof SAMPLES;

/** What each kind of run is called and why it looks the way it does: one sentence each, shared
 *  by every view's popover. */
const MEANING: Record<Kind, { title: string; body: string }> = {
  scored: {
    title: "Scored",
    body: "Finished on the agent’s own terms and graded by the checks and the judge. Only these runs are in the median.",
  },
  partial: {
    title: "Partial judge",
    body: "The judge scored part of its rubric. Solid up to the lower bound, hatched to the upper; it counts at the lower bound.",
  },
  no_judge: {
    title: "No judge",
    body: "The judge never ran, so half the score is unmeasured, not zero. Left out of the median.",
  },
  ended_early: {
    title: "Timed out or stopped",
    body: "Graded on what it did, but it did not finish on its own terms. Left out of the median.",
  },
  infra_failed: {
    title: "Environment failure",
    body: "Cut short by our environment, not the agent. Never graded, never a score.",
  },
  grading: {
    title: "Grading",
    body: "Finished; the grade is not recorded yet. It fills in by itself.",
  },
};

const KINDS: Record<KeyView, Kind[]> = {
  strip: ["scored", "partial", "no_judge", "ended_early", "infra_failed", "grading"],
  plot: ["scored", "partial", "no_judge", "ended_early"],
  ledger: ["scored", "partial", "no_judge", "ended_early", "infra_failed", "grading"],
};

/** One kind of run, drawn by the view's own renderer at key size (`big` for the popover). */
function Swatch({ view, kind, big = false }: { view: KeyView; kind: Kind; big?: boolean }) {
  const run = SAMPLES[kind];
  if (view === "strip") {
    return (
      <span aria-hidden className={cn("flex", big ? "h-24 w-5" : "h-5 w-2.5")}>
        <StripColumn run={run} sample />
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
  if (kind === "partial") {
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
    kind === "ended_early" ? PLOT_MARK.ended_early : kind === "no_judge" ? PLOT_MARK.no_judge : PLOT_MARK.scored;
  return <span aria-hidden className={cn("shrink-0", size, look)} />;
}

const SHORT: Record<Kind, string> = {
  scored: "scored",
  partial: "partial judge",
  no_judge: "no judge",
  ended_early: "timed out or stopped",
  infra_failed: "environment failure",
  grading: "grading",
};

/** The view's own encoding, one line above its charts, and the "How to read this" popover. One
 *  per view, not per card: every card on a page draws the same way. */
export function RunKey({
  view,
  bare = false,
  className,
}: {
  view: KeyView;
  /** Inside a card already: drop the strip's own frame. */
  bare?: boolean;
  className?: string;
}) {
  // `relative` here, not on the trigger: the popover anchors to the whole strip, so it is as wide
  // as the strip (up to 40rem) and never runs past the edge of a narrow screen.
  return (
    <div
      className={cn(
        "relative flex flex-wrap items-center gap-x-4 gap-y-2",
        !bare && "rounded-md border border-border bg-card px-3 py-2",
        className,
      )}
    >
      <ul aria-label="Key" className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-caption text-text-secondary">
        <EncodingItem view={view} />
        {KINDS[view].map((kind) => (
          <li key={kind} className="flex items-center gap-1.5">
            <Swatch view={view} kind={kind} />
            {SHORT[kind]}
          </li>
        ))}
        {view === "strip" && (
          <li className="flex items-center gap-1.5">
            <span aria-hidden className="w-4 border-t border-dashed border-primary" />
            median
          </li>
        )}
      </ul>
      <HowToRead view={view} className="ml-auto" />
    </div>
  );
}

/** What position and colour mean in this view, the first item of its key. */
function EncodingItem({ view }: { view: KeyView }) {
  if (view === "plot") {
    return (
      <li className="flex items-center gap-1.5">
        <span className="text-data">checks →</span>
        <span className="text-chart-4">judge ↑</span>
      </li>
    );
  }
  return (
    <li className="flex items-center gap-1.5">
      <span aria-hidden className="flex size-3 flex-col">
        <span className="flex-1 bg-chart-4" />
        <span className="flex-1 bg-data" />
      </span>
      {view === "strip" ? "judge · checks, height = overall" : "judge · checks, brighter = higher"}
    </li>
  );
}

const READING: Record<KeyView, { lead: string; extra: ReactNode }> = {
  strip: {
    lead: "One column per run, oldest to newest; the card shows as many recent runs as fit. A column’s height is the run’s overall score: the lower part is what the deterministic checks earned, the upper part what the judge earned. Each carries half of the score.",
    extra: (
      <p>
        The dashed line is the <strong className="text-heading">median</strong> of the scored
        runs, over all of the agent’s runs, not only the ones that fit. Every other run is
        counted and named under the card.
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

/** The "How to read this" popover: the view's anatomy, each kind of run drawn large with what it
 *  means. Closes on Escape (focus returns to the trigger), an outside press, or its close button. */
function HowToRead({ view, className }: { view: KeyView; className?: string }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const reading = READING[view];
  return (
    <div ref={wrap} className={className}>
      <button
        ref={trigger}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 rounded-sm text-caption text-text-secondary transition-colors hover:text-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <CircleHelp className="size-3.5" aria-hidden />
        How to read this
      </button>
      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-labelledby={titleId}
          className="absolute right-0 top-full z-30 mt-2 max-h-[70vh] w-full max-w-[40rem] space-y-4 overflow-y-auto rounded-md border border-border-hover bg-raised p-4 shadow-lg"
        >
          <div className="flex items-start justify-between gap-3">
            <p id={titleId} className="text-row font-bold text-heading">
              How to read this
            </p>
            <button
              type="button"
              aria-label="Close"
              onClick={() => {
                setOpen(false);
                trigger.current?.focus();
              }}
              className="rounded-sm text-text-tertiary hover:text-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>
          <p className="text-dense text-text-secondary">{reading.lead}</p>
          <ul className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-3">
            {KINDS[view].map((kind) => (
              <li key={kind} className="flex items-end gap-3">
                <span className="flex h-24 shrink-0 items-end">
                  <Swatch view={view} kind={kind} big />
                </span>
                <span className="space-y-1">
                  <span className="block text-caption font-bold text-heading">
                    {MEANING[kind].title}
                  </span>
                  <span className="block text-caption text-text-secondary">
                    {MEANING[kind].body}
                  </span>
                </span>
              </li>
            ))}
          </ul>
          <div className="space-y-2 text-dense text-text-secondary">{reading.extra}</div>
        </div>
      )}
    </div>
  );
}
