import { Card, CardContent } from "@/components/ui/card";
import { pct } from "@/lib/api/format";
import { cn } from "@/components/ui/cn";
import type { GradeResult, RunStats, RunEntry } from "@/lib/api/types";

const DASH = "—";

// Compact tokens: 13300 → "13.3k", 2_100_000 → "2.1M". Small ints render as-is.
function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

// Seconds → "1m 30s" / "45s" / "1h 2m". Whole-second granularity.
function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

function elapsedFrom(stats: RunStats | undefined, run: RunEntry): string {
  const fromStats = stats?.timing?.elapsed_seconds;
  if (fromStats != null && fromStats > 0) return formatElapsed(fromStats);
  // Fall back to the run row's own clock (always available, even with no telemetry).
  if (run.created_at && run.completed_at) {
    const secs =
      (new Date(run.completed_at).getTime() - new Date(run.created_at).getTime()) / 1000;
    if (secs > 0) return formatElapsed(secs);
  }
  return DASH;
}

// The at-a-glance KPI row: scores (always) + telemetry (— when absent). Debugger's first read.
export function KpiStrip({
  grade,
  stats,
  run,
}: {
  grade: GradeResult;
  stats: RunStats | undefined;
  run: RunEntry;
}) {
  // Guard every nested field: a terminal-ungraded run (e.g. mid-re-evaluate) can briefly surface a
  // gradeless stats placeholder with no tokens/counts/timing — degrade to "—", never crash.
  const totalTokens = stats?.tokens?.total ?? 0;
  const range = (lower: number, upper: number | null | undefined) =>
    upper != null && upper > lower + 1e-9 ? `${pct(lower)}–${pct(upper)}` : pct(lower);
  const tiles: { label: string; value: string; tone?: "score" }[] = [
    { label: "Overall", value: range(grade.overall, grade.overall_upper), tone: "score" },
    { label: "Deterministic", value: pct(grade.breakdown.deterministic) },
    { label: "Judge", value: range(grade.breakdown.judge, grade.judge_upper) },
    { label: "Elapsed", value: elapsedFrom(stats, run) },
    { label: "Tokens", value: totalTokens > 0 ? formatTokens(totalTokens) : DASH },
    {
      label: "Tool calls",
      value: stats?.counts ? String(stats.counts.tool_calls) : DASH,
    },
    {
      label: "Model calls",
      value: stats?.counts ? String(stats.counts.model_calls) : DASH,
    },
  ];
  return (
    <Card className="bg-raised">
      <CardContent className="grid grid-cols-2 gap-x-4 gap-y-4 p-4 sm:grid-cols-4 lg:grid-cols-7">
        {tiles.map((t) => (
          <div key={t.label} className="flex flex-col gap-1">
            <span className="text-label uppercase text-text-tertiary">
              {t.label}
            </span>
            <span
              className={cn(
                "text-lg font-bold tabular-nums",
                t.tone === "score" ? "text-heading" : "text-foreground",
              )}
            >
              {t.value}
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
