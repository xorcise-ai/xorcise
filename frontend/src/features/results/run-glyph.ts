// How one finished run is drawn, shared by the run strip, the grader agreement plot and the run
// ledger so the three cannot draw the same run differently. The server decides how a run counts
// (RunScore.bucket, reporting.scores); this only turns that into shapes and words.

import { pct, shortTime } from "@/lib/api/format";
import type { RunScore, ScoreBucket } from "@/lib/api/types";

/** The parts of a run's column, each a fraction of the full 0–100 height. The deterministic
 *  checks and the judge each carry half of `overall`. */
export interface RunGlyph {
  bucket: ScoreBucket;
  /** Earned by the deterministic checks: deterministic × ½. */
  det: number;
  /** Earned by the judge, solid: judge × ½ (the lower bound when the judge scored part of it). */
  judge: number;
  /** A partly-judged run's spread: from its lower bound to its upper bound. */
  judgeRange: number;
  /** The judge half that was never measured, because the judge never ran: always ½. */
  unmeasured: number;
  /** Timed out or stopped: graded, but not on the agent's own terms. */
  faded: boolean;
  /** No score to draw: an environment failure, or a grade not recorded yet. */
  empty: boolean;
  /** One mark under the column for a run that is not in the median. */
  mark: "" | "×" | "○" | "?" | "…";
  /** The whole run in words, for a tooltip. */
  label: string;
}

const half = (v: number | null | undefined) => (v ?? 0) / 2;

/** What each bucket is called, wherever it is counted or listed. */
export const BUCKET_LABEL: Record<ScoreBucket, string> = {
  scored: "scored",
  no_judge: "no judge",
  ended_early: "timed out or stopped",
  infra_failed: "environment failure",
  grading: "grading",
};

function endedEarlyWord(r: RunScore): string {
  return r.terminal_trigger === "operator" ? "stopped by an operator" : "timed out";
}

function judgeWords(r: RunScore): string {
  if (r.judge_status === "partial" && r.judge_upper != null)
    return `judge ${pct(r.judge)}–${pct(r.judge_upper)} (part of the rubric)`;
  if (r.bucket === "no_judge") return "no judge";
  return `judge ${pct(r.judge)}`;
}

function overallWords(r: RunScore): string {
  if (r.bucket === "no_judge") return `overall ${pct(r.overall)} with the judge half unmeasured`;
  if (r.overall_upper != null && r.overall != null && r.overall_upper > r.overall)
    return `overall ${pct(r.overall)}–${pct(r.overall_upper)}`;
  return `overall ${pct(r.overall)}`;
}

/** One sentence for a run, as a tooltip or a screen-reader list item would read it. */
export function runSentence(r: RunScore): string {
  const when = shortTime(r.completed_at ?? r.created_at);
  switch (r.bucket) {
    case "infra_failed":
      return `${when} · environment failure: not graded, and not an attempt by the agent`;
    case "grading":
      return `${when} · finished, grade not recorded yet`;
    case "ended_early":
      return `${when} · ${endedEarlyWord(r)} · deterministic ${pct(r.deterministic)} · ${judgeWords(r)} · ${overallWords(r)} · not in the median`;
    case "no_judge":
      return `${when} · deterministic ${pct(r.deterministic)} · no judge · ${overallWords(r)} · not in the median`;
    default:
      return `${when} · deterministic ${pct(r.deterministic)} · ${judgeWords(r)} · ${overallWords(r)}`;
  }
}

export function runGlyph(r: RunScore): RunGlyph {
  const base = { bucket: r.bucket, label: runSentence(r) };
  if (r.bucket === "infra_failed" || r.bucket === "grading") {
    return {
      ...base,
      det: 0,
      judge: 0,
      judgeRange: 0,
      unmeasured: 0,
      faded: false,
      empty: true,
      mark: r.bucket === "infra_failed" ? "×" : "…",
    };
  }
  const noJudge = r.bucket === "no_judge";
  const partial = r.judge_status === "partial" && r.judge_upper != null;
  return {
    ...base,
    det: half(r.deterministic),
    judge: noJudge ? 0 : half(r.judge),
    judgeRange: partial ? Math.max(0, half(r.judge_upper) - half(r.judge)) : 0,
    unmeasured: noJudge ? 0.5 : 0,
    faded: r.bucket === "ended_early",
    empty: false,
    mark: r.bucket === "ended_early" ? "○" : noJudge ? "?" : "",
  };
}

/** "2 no judge · 1 timed out or stopped · 1 environment failure": the runs a summary counts
 *  apart from its median, in one line. Empty when there are none. */
export function asideLine(s: {
  no_judge: number;
  ended_early: number;
  infra_failed: number;
  grading: number;
}): string {
  const parts: string[] = [];
  if (s.no_judge) parts.push(`${s.no_judge} ${BUCKET_LABEL.no_judge}`);
  if (s.ended_early) parts.push(`${s.ended_early} ${BUCKET_LABEL.ended_early}`);
  if (s.infra_failed)
    parts.push(`${s.infra_failed} environment ${s.infra_failed === 1 ? "failure" : "failures"}`);
  if (s.grading) parts.push(`${s.grading} ${BUCKET_LABEL.grading}`);
  return parts.join(" · ");
}

/** The hatching that marks a range rather than a measured value, in a given colour. */
export function hatch(color: string): string {
  return `repeating-linear-gradient(135deg, ${color} 0 2px, transparent 2px 5px)`;
}
