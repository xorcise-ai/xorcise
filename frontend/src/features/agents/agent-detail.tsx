"use client";

import Link from "next/link";
import { Pencil, Play } from "lucide-react";
import { useRouter } from "next/navigation";
import { useAgent, useAgentHistory } from "./queries";
import { useRuns } from "@/features/runs/queries";
import { useMissions } from "@/features/missions/queries";
import { useRunScores, useScoreSummary } from "@/features/results/queries";
import { ledgerRows } from "@/features/results/run-ledger";
import { AgentRunHistory, buildAgentRunRows } from "./agent-run-history";
import { AgentPerformance, AgentCoverage } from "./agent-performance";
import { HARNESSES, GenericMark, harnessLabel } from "./harnesses";
import { DeleteAgentButton } from "./delete-agent-dialog";
import { NotFoundState } from "@/components/layout/not-found-state";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/components/ui/cn";
import { Badge } from "@/components/ui/badge";
import { StatTile } from "@/components/ui/stat-tile";
import { shortTime } from "@/lib/api/format";

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-2 text-label uppercase text-text-tertiary">
      {children}
    </h2>
  );
}

export function AgentDetail({ name }: { name: string | null }) {
  const agent = useAgent(name ?? "");
  const history = useAgentHistory(name ?? "");
  const runs = useRuns();
  const missions = useMissions();
  const router = useRouter();
  // The performance views key on the agent's id, known once the agent has loaded.
  const agentId = agent.data?.id ?? "";
  const scores = useRunScores({ agent: agentId }, !!agentId);
  const overall = useScoreSummary("agent", { agent: agentId }, !!agentId);
  const versions = useScoreSummary("mission", { agent: agentId }, !!agentId);

  if (!name) {
    return (
      <NotFoundState
        title="Agent not found"
        message="No agent specified."
        backHref="/agents"
        backLabel="Back to agents"
      />
    );
  }
  if (agent.isLoading) {
    return (
      <div
        role="status"
        aria-label="Loading agent…"
        className="mx-auto max-w-4xl space-y-4 p-6"
      >
        <Skeleton variant="title" className="w-64" />
        <Skeleton variant="text" className="w-full" />
        <SkeletonRows count={4} className="mt-6" />
      </div>
    );
  }
  if (!agent.data) {
    return (
      <NotFoundState
        title="Agent not found"
        message={`No agent named “${name}”.`}
        backHref="/agents"
        backLabel="Back to agents"
      />
    );
  }

  const a = agent.data;
  const harness = HARNESSES.find((h) => h.kind === a.kind);
  const HarnessLogo = harness?.Logo ?? GenericMark;
  const harnessName = harnessLabel(a.kind);
  const telemetry = a.otel
    ? "Configured"
    : a.endpoint
      ? "Endpoint set"
      : "Not configured";

  const rows = buildAgentRunRows(
    history.data ?? [],
    runs.data,
    missions.data,
  );
  // An environment failure is never graded, so it is in no history entry; the ledger shows it.
  const hasHistory = (history.data?.length ?? 0) > 0 || (scores.data?.length ?? 0) > 0;
  const missionName = (slug: string) =>
    missions.data?.find((m) => m.mission_id === slug)?.name ?? slug;
  const ledger = ledgerRows(versions.data ?? [], scores.data ?? [], missionName);

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-primary" aria-hidden>
              <HarnessLogo />
            </span>
            <h1 className="text-lead text-heading">{a.name}</h1>
            <Badge variant="muted">v{a.version ?? 1}</Badge>
            <Badge variant="info">{harnessName}</Badge>
            {a.otel && <Badge variant="ok">Telemetry</Badge>}
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
            <StatTile label="Harness" value={harnessName} small />
            <StatTile label="Model" value={a.model ?? "—"} small />
            <StatTile label="Telemetry" value={telemetry} small />
            <StatTile label="Registered" value={shortTime(a.created_at)} small />
          </dl>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* One create-run experience — deep-link into /runs/new with this agent
              preselected instead of a second, cut-down modal flow. */}
          <Link
            href={`/runs/new?agent=${encodeURIComponent(a.name)}`}
            className={cn(buttonVariants())}
          >
            <Play className="size-4" />
            Start run
          </Link>
          <Button
            variant="outline"
            onClick={() =>
              router.push(`/agents/new?agent=${encodeURIComponent(a.name)}`)
            }
          >
            <Pencil className="size-4" />
            Edit
          </Button>
          <DeleteAgentButton
            name={a.name}
            onDeleted={() => router.push("/agents")}
          />
        </div>
      </div>

      {hasHistory && (
        <>
          <section>
            <SectionHeading>Performance</SectionHeading>
            <AgentPerformance
              summary={overall.data?.[0]}
              summaryState={
                overall.isLoading ? "loading" : overall.isError ? "error" : "ready"
              }
              ledger={ledger}
              ledgerState={
                versions.isLoading || scores.isLoading
                  ? "loading"
                  : versions.isError || scores.isError
                    ? "error"
                    : "ready"
              }
            />
          </section>

          <section>
            <SectionHeading>Mission coverage</SectionHeading>
            <AgentCoverage rows={rows} />
          </section>
        </>
      )}

      <section>
        <SectionHeading>Run history</SectionHeading>
        <AgentRunHistory
          rows={rows}
          isLoading={history.isLoading}
          isError={history.isError}
        />
      </section>
    </div>
  );
}
