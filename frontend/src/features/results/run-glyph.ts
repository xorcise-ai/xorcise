// How one finished run is drawn, shared by the run strip, the grader agreement plot and the run
// ledger so the three cannot draw the same run differently. The server decides how a run counts
// (RunScore.bucket, reporting.scores); this only turns that into shapes and words.

import { pct, shortTime } from "@/lib/api/format";
import type { RunScore, ScoreBucket } from "@/lib/api/types";

/**
 * What a run is, for drawing and naming it: the server's `bucket`, with a scored run told apart by
 * how it was scored. One kind per run; every view draws and names a run by its kind.
 */
export type RunKind =
  | "scored"
  | "timed_out"
  | "partial_judge"
  | "no_judge"
  | "stopped"
  | "grading_failed"
  | "infra_failed"
  | "grading";

export function runKind(r: RunScore): RunKind {
  switch (r.bucket) {
    case "infra_failed":
    case "grading":
    case "stopped":
    case "grading_failed":
    case "no_judge":
      return r.bucket;
    default:
      if (r.timed_out) return "timed_out";
      if (r.judge_status === "partial" && r.judge_upper != null) return "partial_judge";
      return "scored";
  }
}

/** How each kind is named and explained, everywhere: the key, the inspector, "How to read this".
 *  `inMedian` is the server's rule (reporting.scores), never decided here; null = not yet graded. */
export const KIND_META: Record<
  RunKind,
  { label: string; title: string; inMedian: boolean | null; body: string }
> = {
  scored: {
    label: "scored",
    title: "Scored",
    inMedian: true,
    body: "Graded by the checks and the judge.",
  },
  timed_out: {
    label: "timed out",
    title: "Timed out",
    inMedian: true,
    body: "Ran out of its time budget. Not finishing counts against the agent, so it is in the median at the score it earned.",
  },
  partial_judge: {
    label: "partial judge",
    title: "Partial judge",
    inMedian: true,
    body: "The judge scored part of its rubric. Counted at the lower bound; the hatching runs to the upper.",
  },
  no_judge: {
    label: "no judge",
    title: "No judge",
    inMedian: false,
    body: "The judge never ran, so half the score is unmeasured, not zero.",
  },
  stopped: {
    label: "stopped",
    title: "Stopped",
    inMedian: false,
    body: "An operator ended it, so its score says nothing about the agent.",
  },
  grading_failed: {
    label: "grading failed",
    title: "Grading failed",
    inMedian: false,
    body: "Grading itself failed, so nothing graded it: not the checks, not the judge. Re-evaluate it to score it.",
  },
  infra_failed: {
    label: "environment failure",
    title: "Environment failure",
    inMedian: false,
    body: "Our environment cut it short. Never graded, and not the agent’s attempt.",
  },
  grading: {
    label: "grading",
    title: "Grading",
    inMedian: null,
    body: "Finished; its grade is not recorded yet.",
  },
};

/** "Timed out · in the median": a kind and whether it counts, as the inspector heads a run. */
export function kindHeading(kind: RunKind): string {
  const { title, inMedian } = KIND_META[kind];
  return `${title} · ${inMedian === null ? "not graded yet" : inMedian ? "in the median" : "left out"}`;
}

/** The parts of a run's column, each a fraction of the full 0–100 height. The deterministic
 *  checks and the judge each carry half of `overall`. */
export interface RunGlyph {
  kind: RunKind;
  bucket: ScoreBucket;
  /** Earned by the deterministic checks: deterministic × ½. */
  det: number;
  /** Earned by the judge, solid: judge × ½ (the lower bound when the judge scored part of it). */
  judge: number;
  /** A partly-judged run's spread: from its lower bound to its upper bound. */
  judgeRange: number;
  /** The judge half that was never measured, because the judge never ran: always ½. */
  unmeasured: number;
  /** An operator stopped it: drawn in grey, because its score says nothing about the agent. */
  grey: boolean;
  /** Ran out of its budget: a red cap on top. Red means only this: the agent did not finish. */
  cap: boolean;
  /** No score to draw: an environment failure (a grey striped slot), a run whose grading failed
   *  (an empty dashed slot), or a grade not recorded yet. */
  empty: boolean;
  /** The whole run in words, for a screen reader or a tooltip. */
  label: string;
}

const half = (v: number | null | undefined) => (v ?? 0) / 2;

/** What each bucket is called, wherever it is counted or listed. */
export const BUCKET_LABEL: Record<ScoreBucket, string> = {
  scored: "scored",
  no_judge: "no judge",
  stopped: "stopped",
  grading_failed: "grading failed",
  infra_failed: "environment failure",
  grading: "grading",
};

/**
 * Did the judge half never run? `unavailable` / `model-not-configured` mean the judge contributed
 * 0.0 to `overall` without grading (#143). `partial` is NOT this: the judge ran on part of the
 * rubric and discloses its own coverage. No status at all is an ungraded run. The CLI's
 * `judge_degraded` (cli/commands/run.py), over the server's `judge_status`, for the per-run tables.
 */
export function judgeDegraded(status: string | null | undefined): boolean {
  return !!status && status !== "ok" && status !== "partial";
}

/** The judge half was never measured: a run with no judge, or a stopped one whose judge never ran
 *  (its bucket says it was stopped, but its judge half is no more a zero than a no-judge run's). */
function judgeUnmeasured(r: RunScore): boolean {
  return r.bucket === "no_judge" || judgeDegraded(r.judge_status);
}

function judgeWords(r: RunScore): string {
  if (r.judge_status === "partial" && r.judge_upper != null)
    return `judge ${pct(r.judge)}–${pct(r.judge_upper)} (part of the rubric)`;
  if (judgeUnmeasured(r)) return "no judge";
  return `judge ${pct(r.judge)}`;
}

function overallWords(r: RunScore): string {
  if (judgeUnmeasured(r)) return `overall ${pct(r.overall)} with the judge half unmeasured`;
  if (r.overall_upper != null && r.overall != null && r.overall_upper > r.overall)
    return `overall ${pct(r.overall)}–${pct(r.overall_upper)}`;
  return `overall ${pct(r.overall)}`;
}

/** Nothing graded the run, so it has no score to show or draw. */
function noScore(r: RunScore): boolean {
  return r.bucket === "infra_failed" || r.bucket === "grading" || r.bucket === "grading_failed";
}

/** A run's scores in the inspector's three cells: "—" where there is no score to show. */
export function runCells(r: RunScore): { checks: string; judge: string; overall: string } {
  const none = noScore(r);
  const judge = none || judgeUnmeasured(r)
    ? "—"
    : r.judge_status === "partial" && r.judge_upper != null
      ? `${pct(r.judge)}–${pct(r.judge_upper)}`
      : pct(r.judge);
  const overall = none
    ? "—"
    : r.overall_upper != null && r.overall != null && r.overall_upper > r.overall
      ? `${pct(r.overall)}–${pct(r.overall_upper)}`
      : pct(r.overall);
  return { checks: none ? "—" : pct(r.deterministic), judge, overall };
}

/** One sentence for a run, as a tooltip or a screen-reader list item would read it. */
export function runSentence(r: RunScore): string {
  const when = shortTime(r.completed_at ?? r.created_at);
  const heading = kindHeading(runKind(r)).toLowerCase();
  switch (r.bucket) {
    case "infra_failed":
      return `${when} · environment failure: not graded, and not an attempt by the agent`;
    case "grading":
      return `${when} · finished, grade not recorded yet`;
    case "grading_failed":
      return `${when} · grading failed: nothing graded it, so it has no score`;
    case "no_judge":
      return `${when} · ${heading} · deterministic ${pct(r.deterministic)} · no judge · ${overallWords(r)}`;
    default:
      return `${when} · ${heading} · deterministic ${pct(r.deterministic)} · ${judgeWords(r)} · ${overallWords(r)}`;
  }
}

export function runGlyph(r: RunScore): RunGlyph {
  const kind = runKind(r);
  const base = { kind, bucket: r.bucket, label: runSentence(r), cap: kind === "timed_out" };
  if (noScore(r)) {
    return { ...base, det: 0, judge: 0, judgeRange: 0, unmeasured: 0, grey: false, empty: true };
  }
  const noJudge = judgeUnmeasured(r);
  const partial = r.judge_status === "partial" && r.judge_upper != null;
  return {
    ...base,
    det: half(r.deterministic),
    judge: noJudge ? 0 : half(r.judge),
    judgeRange: partial ? Math.max(0, half(r.judge_upper) - half(r.judge)) : 0,
    unmeasured: noJudge ? 0.5 : 0,
    grey: r.bucket === "stopped",
    empty: false,
  };
}

/** "2 no judge · 1 stopped · 1 grading failed · 1 environment failure": the runs a summary counts apart from its
 *  median, in one line. Empty when there are none. */
export function asideLine(s: {
  no_judge: number;
  stopped: number;
  grading_failed?: number;
  infra_failed: number;
  grading: number;
}): string {
  const parts: string[] = [];
  if (s.no_judge) parts.push(`${s.no_judge} ${BUCKET_LABEL.no_judge}`);
  if (s.stopped) parts.push(`${s.stopped} ${BUCKET_LABEL.stopped}`);
  if (s.grading_failed) parts.push(`${s.grading_failed} ${BUCKET_LABEL.grading_failed}`);
  if (s.infra_failed)
    parts.push(`${s.infra_failed} environment ${s.infra_failed === 1 ? "failure" : "failures"}`);
  if (s.grading) parts.push(`${s.grading} ${BUCKET_LABEL.grading}`);
  return parts.join(" · ");
}

/** "2 timed out: counted at the score they earned." Empty when none did. Timed-out runs are in
 *  the median, so this sits apart from {@link asideLine}, which lists the runs that are not. */
export function timedOutLine(s: { timed_out: number }): string {
  if (!s.timed_out) return "";
  return `${s.timed_out} timed out: in the median at the score ${s.timed_out === 1 ? "it" : "they"} earned.`;
}

/** The hatching that marks a range rather than a measured value, in a given colour. */
export function hatch(color: string): string {
  return `repeating-linear-gradient(135deg, ${color} 0 2px, transparent 2px 5px)`;
}
