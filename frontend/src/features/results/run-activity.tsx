import Link from "next/link";
import { Map as MapIcon, ExternalLink } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/components/ui/cn";
import type { RunStats } from "@/lib/api/types";
import { useConfig } from "@/features/settings/queries";
import { TerrainMap } from "@/features/live-trace/terrain-map";
import { useRunStats } from "./queries";

// What the agent DID: the final terrain map (frozen) + a transcript summary, with a click-through
// to the full interactive replay on the live-trace page. The results page shows the outcome; the
// live page remains the home of the step-by-step replay.
export function RunActivity({ runId }: { runId: string }) {
  const stats = useRunStats(runId);
  const cfg = useConfig();
  // Mirror the live page: with no attribution model configured, the mission plane can't light
  // up — TerrainMap shows a notice instead of leaving an empty map mysterious.
  const attributionOff = cfg.data ? !cfg.data.terrain.configured : false;

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-label uppercase text-text-tertiary">
          <MapIcon className="size-3.5 text-primary" />
          Activity
        </h2>
        <Link
          href={`/runs/live?id=${encodeURIComponent(runId)}`}
          className="inline-flex items-center gap-1 text-caption text-primary underline-offset-2 hover:underline"
        >
          View full trace <ExternalLink className="size-3" />
        </Link>
      </div>

      <TranscriptSummary stats={stats.data} />

      {/* The final terrain map — frozen (active=false), so it renders the run's end state. */}
      <Card className="overflow-hidden">
        <CardHeader className="py-2">
          <CardTitle className="text-label uppercase text-text-tertiary">
            Terrain
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4">
          <TerrainMap runId={runId} active={false} attributionOff={attributionOff} />
        </CardContent>
      </Card>
    </section>
  );
}

// A compact "what happened" line from the persisted counts — model calls, tool calls, findings,
// errors. Renders a quiet placeholder when no telemetry was captured (no adapter / empty run).
export function TranscriptSummary({ stats }: { stats: RunStats | undefined }) {
  if (!stats || stats.counts.events_total === 0) {
    return (
      <Card className="bg-card">
        <CardContent className="p-4">
          <p className="text-body text-text-secondary">
            No transcript telemetry was captured for this run.
          </p>
        </CardContent>
      </Card>
    );
  }
  const c = stats.counts;
  const items: { label: string; value: number; tone?: "err" }[] = [
    { label: "Model calls", value: c.model_calls },
    { label: "Tool calls", value: c.tool_calls },
    { label: "Findings", value: c.findings },
    { label: "Errors", value: c.errors, tone: "err" },
  ];
  return (
    <Card className="bg-card">
      <CardContent className="flex flex-wrap gap-x-8 gap-y-3 p-4">
        {items.map((it) => (
          <div key={it.label} className="flex flex-col gap-1">
            <span className="text-label uppercase text-text-tertiary">
              {it.label}
            </span>
            <span
              className={cn(
                "text-base font-semibold tabular-nums",
                it.tone === "err" && it.value > 0 ? "text-err" : "text-foreground",
              )}
            >
              {it.value}
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
