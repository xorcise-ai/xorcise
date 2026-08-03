import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { pct } from "@/lib/api/format";
import { cn } from "@/components/ui/cn";
import type { GradeResult } from "@/lib/api/types";

export type Tone = { hex: string; text: string };

// Maps a 0..1 overall onto the semantic pass/marginal/fail palette. Mirrors
// the old completion-rate rail: ≥80% green, ≥50% amber, else red.
export function scoreTone(value: number): Tone {
  if (value >= 0.8) return { hex: "#6ee7a8", text: "text-ok" };
  if (value >= 0.5) return { hex: "#e8b84b", text: "text-primary" };
  return { hex: "#ff5f57", text: "text-err" };
}

// The graded scorecard: overall ring + the two half-score bars + a checks-passed line.
export function Scorecard({ grade: r, tone }: { grade: GradeResult; tone: Tone }) {
  const checksPassed = r.check_breakdown.filter((c) => c.passed).length;
  const checksTotal = r.check_breakdown.length;
  const judgeUpper = r.judge_upper ?? r.breakdown.judge;
  const hasJudgeRange = judgeUpper > r.breakdown.judge + 1e-9;
  return (
    <Card
      className="overflow-hidden bg-raised"
      style={{ borderLeftWidth: 3, borderLeftColor: tone.hex }}
    >
      {/* 16 inside the card against the page's 24 between sections is a real
          step; 20-vs-24 was not, which is why every slab read at one weight. */}
      <CardContent className="space-y-4 p-4">
        <p className="text-label uppercase text-text-tertiary">
          Scorecard
        </p>

        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          {/* Overall ring */}
          <ScoreRing label="Overall" value={r.overall} tone={tone} />

          {/* Breakdown bars */}
          <div className="min-w-0 flex-1 space-y-3">
            <BreakdownBar
              label="Deterministic"
              value={r.breakdown.deterministic}
              helpText="Hard checks scored by the control plane — flag match, artefact presence, deterministic asserts."
            />
            <BreakdownBar
              label="Judge"
              value={r.breakdown.judge}
              helpText={
                hasJudgeRange
                  ? `Conservative lower bound; possible range ${pct(r.breakdown.judge)}–${pct(judgeUpper)} at ${pct(r.judge_coverage ?? 0)} coverage.`
                  : "LLM-judge rubric score (50% of overall) — 0 when no judge ran."
              }
              muted={r.judge_status !== "ok" && r.judge_status !== "partial"}
            />
          </div>
        </div>

        {checksTotal > 0 && (
          <>
            <Separator />
            <p className="text-body text-text-secondary">
              <strong className="text-foreground">{checksPassed}</strong> of{" "}
              <strong className="text-foreground">{checksTotal}</strong>{" "}
              deterministic check{checksTotal !== 1 ? "s" : ""} passed
              {r.judge_breakdown.length > 0 && (
                <>
                  {" · "}
                  <strong className="text-foreground">
                    {r.judge_breakdown.length}
                  </strong>{" "}
                  judge criteri
                  {r.judge_breakdown.length !== 1 ? "a" : "on"}
                </>
              )}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// The headline number, drawn as a conic-gradient ring so the scorecard reads
// at a glance the way the old rings/bars did. Pure CSS — no external dep.
export function ScoreRing({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: Tone;
}) {
  const deg = Math.round(Math.max(0, Math.min(1, value ?? 0)) * 360);
  return (
    <div className="flex flex-col items-center gap-2">
      <div
        className="grid size-28 place-items-center rounded-full"
        style={{
          background: `conic-gradient(${tone.hex} ${deg}deg, var(--color-muted) 0deg)`,
        }}
      >
        <div className="grid size-[5.5rem] place-items-center rounded-full bg-raised">
          <span className={cn("text-3xl font-bold tabular-nums", tone.text)}>
            {pct(value)}
          </span>
        </div>
      </div>
      <span className="text-label uppercase text-text-tertiary">
        {label}
      </span>
    </div>
  );
}

export function BreakdownBar({
  label,
  value,
  helpText,
  muted,
}: {
  label: string;
  value: number;
  helpText?: string;
  muted?: boolean;
}) {
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="text-label uppercase text-text-tertiary">
          {label}
        </span>
        <span
          className={cn(
            "text-dense font-semibold tabular-nums",
            muted ? "text-text-tertiary" : "text-primary",
          )}
        >
          {pct(value)}
        </span>
      </div>
      <Progress value={Math.round((value ?? 0) * 100)} />
      {helpText && (
        <p className="mt-1 text-caption text-text-tertiary">
          {helpText}
        </p>
      )}
    </div>
  );
}
