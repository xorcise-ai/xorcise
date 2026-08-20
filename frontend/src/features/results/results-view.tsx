"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, RotateCw } from "lucide-react";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { NotFoundState } from "@/components/layout/not-found-state";
import { Page, PageBody, PageHead, PageTitle } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { cn } from "@/components/ui/cn";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import { fullTime, formatDuration } from "@/lib/api/format";
import type { GradeResult, RunEntry } from "@/lib/api/types";
import { useRun } from "@/features/runs/queries";
import { useAgents } from "@/features/agents/queries";
import { DeleteRunButton } from "@/features/runs/delete-run-dialog";
import { useRunResult, useRunStats, useRegradeRun } from "./queries";
import { KpiStrip } from "./kpi-strip";
import { CrossRunContext } from "./cross-run-context";
import { RunActivity } from "./run-activity";
import { Scorecard, scoreTone } from "./scorecard";
import { Verdict } from "./verdict";
import { GradingDetail } from "./grading-detail";
import { ArtifactsSection } from "./artifacts-section";
import { ConditionsCard } from "./conditions-card";
import { DownloadReport } from "./download-report";
import { DownloadTraces } from "./download-traces";

export function ResultsView({ runId }: { runId: string | null }) {
  const result = useRunResult(runId ?? "");
  const run = useRun(runId ?? "");
  const agents = useAgents();
  const stats = useRunStats(runId ?? "");
  const router = useRouter();
  const regrade = useRegradeRun(runId ?? "");
  // Set when the operator triggers a re-evaluate, so the transient "grading" state that follows
  // reads as "Re-evaluating…" rather than a first-time grade.
  const [reevaluating, setReevaluating] = useState(false);

  if (!runId)
    return (
      <NotFoundState
        title="Result not found"
        message="No run specified."
        backHref="/runs"
        backLabel="Back to runs"
      />
    );
  if (result.isLoading)
    return (
      <div role="status" aria-label="Loading result…" className="space-y-4 p-4">
        <Skeleton variant="title" className="w-64" />
        <SkeletonRows count={6} />
      </div>
    );
  if (result.isError) {
    if ((result.error as ApiError)?.status === 404)
      return (
        <NotFoundState
          title="Result not found"
          message={`No result for “${runId}”.`}
          backHref="/runs"
          backLabel="Back to runs"
        />
      );
    return (
      <div className="p-4">
        <p className="text-body text-err">Couldn’t load the result.</p>
      </div>
    );
  }

  const view = result.data!;
  // GET /runs/{id}/result now returns RunResultView — the grade lives under
  // `grade`, with disclosed conditions + partial state alongside it.
  //
  // A terminal-but-ungraded run has no grade yet: grading runs async after the
  // run completes, so the endpoint returns 202 {status:"grading"},
  // which api.get surfaces as a gradeless body. Treat `grade` as possibly absent
  // and render a graceful state instead of crashing on r.overall.
  const r = view.grade as GradeResult | undefined;
  const conditions = view.conditions;

  if (!r) return <GradingState runId={runId} reevaluating={reevaluating} />;

  // Score → accent. A passing scorecard reads green, a marginal one amber,
  // a failing one red. Drives the header rail + the big number's colour.
  const tone = scoreTone(r.overall);

  return (
    // A run result is a document, not a dashboard: one centred reading column that stacks the
    // sections top-to-bottom (the layout the operator asked to keep). The Page frame keeps the head
    // as fixed chrome with the Download action and makes the body the single scroller — so it reads
    // as one column WITHOUT the two-pane split that stranded a tall empty rail beside short runs.
    <Page className="gap-3">
      <PageHead>
        <PageTitle
          eyebrow="Run Result"
          subtitle={
            <span className="font-mono text-text-tertiary">{r.run_id}</span>
          }
        >
          {run.data?.name ?? "Result"}
        </PageTitle>
        <div className="flex flex-wrap items-center gap-2">
          {/* Re-evaluate: re-grade the saved evidence with the current judge settings — no new
              agent run. The natural fix after a run whose judge half failed on a config limit. */}
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setReevaluating(true);
              regrade.mutate();
            }}
            disabled={regrade.isPending}
            aria-busy={regrade.isPending}
            title="Re-grade this run's saved evidence with the current judge settings (no new run)"
          >
            {/* The spin is motion-safe, so a reduced-motion reader sees a frozen icon — the
                label carries the busy state on its own (the idiom used by every other
                in-flight button: "Pulling…", "Saving…", "Starting…"). */}
            <RotateCw
              className={cn(
                "size-3.5",
                regrade.isPending && "motion-safe:animate-spin",
              )}
            />
            {regrade.isPending ? "Re-evaluating…" : "Re-evaluate"}
          </Button>
          {/* Shareable, offline copy of everything below (Content-Disposition drives the name). */}
          <DownloadReport runId={r.run_id} />
          {/* The raw OTLP evidence behind the report, for external OTel tooling — always
              available, even while grading. */}
          <DownloadTraces runId={r.run_id} />
        </div>
      </PageHead>

      <PageBody>
        <div className="mx-auto w-full max-w-4xl space-y-3">
          {/* ═══ RUN METADATA — mission · agent · versions · date/time, clearly labelled ═══ */}
          {run.data && (
            <RunMetaBar
              run={run.data}
              agentName={
                agents.data?.find((a) => a.id === run.data!.agent_id)?.name ??
                run.data.agent_id.slice(0, 8)
              }
            />
          )}

          {/* ═══ PARTIAL BANNER — run didn't finish on the agent's terms ═══ */}
          {view.partial && (
            <Card
              className="bg-primary/10"
              style={{
                borderLeftWidth: 3,
                borderLeftColor: "var(--color-primary)",
              }}
            >
              <CardContent className="flex items-start gap-3 p-4">
                <span aria-hidden className="mt-0.5 shrink-0 text-primary">
                  ⚠
                </span>
                <div>
                  <p className="text-body font-medium text-foreground">
                    {view.partial_trigger === "operator"
                      ? "Partial — stopped early"
                      : "Partial — timed out"}
                  </p>
                  <p className="mt-2 max-w-[70ch] text-body text-text-secondary">
                    {view.partial_trigger === "operator"
                      ? "This run was terminated by the operator before it finished."
                      : "This run did not finish before its deadline."}
                    {view.partial_trigger ? (
                      <>
                        {" "}
                        (
                        <span className="font-mono text-text-tertiary">
                          {view.partial_trigger}
                        </span>
                        )
                      </>
                    ) : null}{" "}
                    Scores below reflect only what was completed.
                  </p>
                </div>
              </CardContent>
            </Card>
          )}

          {/* ═══ KPI STRIP — scores + telemetry at a glance (run-report) ═══ */}
          {run.data && <KpiStrip grade={r} stats={stats.data} run={run.data} />}

          {/* ═══ SCORECARD ═══ */}
          <Scorecard grade={r} tone={tone} />

          {/* ═══ CROSS-RUN CONTEXT — this run vs the agent's other runs on this mission ═══ */}
          {run.data && <CrossRunContext run={run.data} thisOverall={r.overall} />}

          {/* ═══ VERDICT — hard fails / evidence ═══ */}
          <Verdict grade={r} />

          {/* ═══ ACTIVITY — what the agent did: final terrain + transcript summary ═══ */}
          <RunActivity runId={r.run_id} />

          {/* ═══ GRADING DETAIL — checks / judge criteria / judge prompt ═══ */}
          <GradingDetail grade={r} />

          {/* ═══ ARTIFACTS — work the agent submitted, with full content ═══ */}
          <ArtifactsSection runId={r.run_id} />

          {/* ═══ CONDITIONS — what this result was produced under ═══ */}
          <ConditionsCard conditions={conditions} />

          {/* ═══ FOOTER — trace ref + deep-link + delete ═══ */}
          {/* The live view hosts the trace; static export routes by ?id= (no /runs/[id] segment). */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
            <span className="truncate font-mono text-caption text-text-tertiary">
              trace ref: {r.trace_ref ?? "—"}
            </span>
            <div className="flex items-center gap-3">
              <Link
                href={`/runs/live?id=${encodeURIComponent(r.run_id)}`}
                className="text-caption text-text-tertiary underline-offset-2 hover:text-primary hover:underline"
              >
                View trace →
              </Link>
              {/* Operator-facing per-run delete; routes back to the runs list on success. */}
              <DeleteRunButton
                runId={r.run_id}
                onDeleted={() => router.push("/runs")}
              />
            </div>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}

/**
 * The run's identity metadata, one canonical set shared with the live run header and the HTML/MD
 * exports: Mission and Agent (each carrying its pinned version in the name), the Harness that ran
 * it, when it Started and how long it ran (Duration). A compact labelled grid so the reader knows
 * exactly which run this is before reading the scores it produced.
 */
function RunMetaBar({ run, agentName }: { run: RunEntry; agentName: string }) {
  const elapsed = run.completed_at
    ? (new Date(run.completed_at).getTime() - new Date(run.created_at).getTime()) / 1000
    : null;
  const items: { label: string; value: string; mono?: boolean }[] = [
    { label: "Mission", value: `${run.mission} v${run.mission_version ?? run.install_revision}` },
    { label: "Agent", value: `${agentName} v${run.agent_version}` },
    { label: "Harness", value: run.source_agent },
    // The architecture the run executed on (§43-UX8) — omitted for a pre-contract run so its
    // bar is unchanged, mono because it is a machine token (linux/arm64).
    ...(run.platform ? [{ label: "Platform", value: run.platform, mono: true }] : []),
    { label: "Started", value: fullTime(run.created_at), mono: true },
    { label: "Duration", value: formatDuration(elapsed), mono: true },
  ];
  return (
    <Card className="bg-raised">
      <CardContent className="grid grid-cols-2 gap-x-4 gap-y-3 p-4 sm:grid-cols-3 lg:grid-cols-5">
        {items.map((it) => (
          <div key={it.label} className="flex min-w-0 flex-col gap-1">
            <span className="text-label uppercase text-text-tertiary">
              {it.label}
            </span>
            <span
              className={cn(
                "min-w-0 truncate text-foreground",
                it.mono ? "font-mono text-dense" : "text-body",
              )}
              title={it.value}
            >
              {it.value}
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

// A terminal run whose grade hasn't been recorded yet — grading runs async after
// the run completes, so the result endpoint returns 202 {status:"grading"}
// with no grade. Render a calm, non-error "in progress" state rather than crashing
// on the absent grade. role="status" (not "alert") — this is transient,
// not a failure.
function GradingState({
  runId,
  reevaluating = false,
}: {
  runId: string;
  reevaluating?: boolean;
}) {
  // Polling re-drives grading server-side (ensure_graded_async), so a wedged run normally resolves
  // within a poll or two — even a failing judge records a fallback result. If it's STILL grading
  // after a beat, the judge model is the likeliest blocker: say so and point to Settings rather
  // than spinning silently ("keeps on dialing").
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setSlow(true), 12000);
    return () => clearTimeout(t);
  }, []);

  return (
    <div
      role="status"
      className="flex h-full min-h-[24rem] flex-col items-center justify-center gap-3 p-4 text-center"
    >
      <Loader2 className="size-8 motion-safe:animate-spin text-primary" />
      <p className="text-body font-semibold text-heading">
        {reevaluating ? "Re-evaluating" : "Grading in progress"}
      </p>
      <p className="max-w-sm text-body text-text-secondary">
        {reevaluating
          ? "Re-grading this run's saved evidence with the current judge settings. The scorecard will update here once it completes."
          : "This run has finished; its result is still being graded. The scorecard will appear here once grading completes."}
      </p>
      {slow && (
        <p className="max-w-sm rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-body text-foreground">
          This is taking longer than usual. If the judge model is unreachable or
          its key is rejected, grading can’t finish —{" "}
          <Link
            href="/settings?focus=judge"
            className="font-medium text-primary underline underline-offset-2"
          >
            check it in Settings →
          </Link>
        </p>
      )}
      <p className="font-mono text-dense text-text-tertiary">{runId}</p>
      <Link
        href="/runs"
        className="mt-2 rounded-md border border-border px-3 py-1.5 text-dense text-foreground transition-colors hover:border-[rgba(255,255,255,0.14)] hover:text-primary"
      >
        Back to runs
      </Link>
    </div>
  );
}
