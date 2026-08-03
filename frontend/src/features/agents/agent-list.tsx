"use client";

import { useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus, Play, X } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Reveal } from "@/components/ui/reveal";
import { cn } from "@/components/ui/cn";
import { Page, PageBody, PageHead, PageTitle } from "@/components/layout/page";
import { useRuns } from "@/features/runs/queries";
import { Facet } from "@/features/runs/run-filters";
import { isTerminal } from "@/features/runs/run-state";
import { useRunResults } from "@/features/results/queries";
import { PerformanceSummary } from "@/features/results/performance-summary";
import {
  summarizeByAgent,
  type AgentRunRow,
  type AgentPerformanceSummary,
} from "@/features/results/summarize-runs";
import { useAgents } from "./queries";
import { HARNESSES, CUSTOM_LABEL, CustomMark } from "./harnesses";
import type { AgentEntry } from "@/lib/api/types";

/** Harness identity for an agent's `kind` (report §7: the harness icon is the visual identity).
 *  Falls back to the neutral custom mark + label when the kind has no registered harness. */
function harnessOf(kind: string | null | undefined): {
  Logo: () => ReactNode;
  name: string;
} {
  const h = kind ? HARNESSES.find((x) => x.kind === kind) : undefined;
  if (h) return { Logo: h.Logo, name: h.name };
  return { Logo: CustomMark, name: kind || CUSTOM_LABEL };
}

/** Facet bucket for an agent's model — agents that disclose no model share one bucket,
 *  phrased like the review panel's "Not disclosed" placeholder. */
const NO_MODEL = "Not disclosed";
function modelOf(agent: AgentEntry): string {
  return agent.model || NO_MODEL;
}

/** Frontend-only agent list filters, mirroring the runs page's facet bar. */
interface AgentFilters {
  harness: string | null;
  model: string | null;
}

const EMPTY_AGENT_FILTERS: AgentFilters = { harness: null, model: null };

export function AgentList() {
  const router = useRouter();
  const agents = useAgents();
  const runs = useRuns();
  const [filters, setFilters] = useState<AgentFilters>(EMPTY_AGENT_FILTERS);

  // Newest first — most recently registered leads the grid. The server serves oldest-first
  // (registry list_agents sorts ascending on created_at), so the page owns its ordering
  // contract rather than trusting transport order; name breaks created_at ties
  // deterministically.
  const sorted = useMemo(
    () =>
      [...(agents.data ?? [])].sort(
        (a, b) =>
          b.created_at.localeCompare(a.created_at) || a.name.localeCompare(b.name),
      ),
    [agents.data],
  );

  // Facet options reflect what is actually registered (like the runs facets), so neither
  // select ever offers a choice that filters to nothing.
  const harnessOptions = useMemo(
    () =>
      [...new Set(sorted.map((a) => harnessOf(a.kind).name))].sort((a, b) =>
        a.localeCompare(b),
      ),
    [sorted],
  );
  const modelOptions = useMemo(
    () =>
      [...new Set(sorted.map(modelOf))].sort(
        // The undisclosed bucket reads as an "other" — keep it after the real models.
        (a, b) =>
          (a === NO_MODEL ? 1 : 0) - (b === NO_MODEL ? 1 : 0) || a.localeCompare(b),
      ),
    [sorted],
  );

  const filtered = sorted.filter(
    (a) =>
      (!filters.harness || harnessOf(a.kind).name === filters.harness) &&
      (!filters.model || modelOf(a) === filters.model),
  );
  const filtersActive = Boolean(filters.harness || filters.model);

  // Per-agent track record, aggregated entirely in the frontend from the runs the app
  // already fetches (mirrors the Results page — no new backend field or endpoint).
  const terminal = useMemo(
    () => (runs.data ?? []).filter(isTerminal),
    [runs.data],
  );
  const results = useRunResults(terminal.map((r) => r.run_id));

  const rows: AgentRunRow[] = terminal.map((r, i) => {
    const view = results[i]?.data;
    const trigger = r.terminal_trigger;
    const partial =
      view?.partial ?? (trigger === "timeout" || trigger === "budget");
    const completedOnOwnTerms = trigger === "done" || trigger === "completed";
    return {
      agentId: r.agent_id,
      agentName: r.agent_id, // name isn't needed here; cards key off the agent list
      overall: view?.grade?.overall ?? null,
      partial,
      completed: completedOnOwnTerms,
      when: r.completed_at ?? r.created_at,
    };
  });

  const summaryByAgentId = useMemo(() => {
    const m = new Map<string, AgentPerformanceSummary>();
    for (const s of summarizeByAgent(rows)) m.set(s.agentId, s);
    return m;
  }, [rows]);

  const scoresLoading = results.some((q) => q.isLoading);

  return (
    <Page className="gap-3">
      <PageHead>
        <PageTitle subtitle="The AI harnesses registered for evaluation, with their track record.">
          Agents
        </PageTitle>
        <Button onClick={() => router.push("/agents/new")}>
          <Plus className="size-4" />
          Register agent
        </Button>
      </PageHead>

      <PageBody className="space-y-3">
        {agents.isError && (
          <p className="text-body text-err">Couldn’t load agents.</p>
        )}

        {agents.isLoading && <LoadingCards />}

        {agents.data && agents.data.length === 0 && (
          <EmptyAgents onRegister={() => router.push("/agents/new")} />
        )}

        {agents.data && agents.data.length > 0 && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Facet
                label="Harness"
                placeholder="All harnesses"
                value={filters.harness}
                options={harnessOptions}
                onChange={(harness) => setFilters({ ...filters, harness })}
              />
              <Facet
                label="Model"
                placeholder="All models"
                value={filters.model}
                options={modelOptions}
                onChange={(model) => setFilters({ ...filters, model })}
              />
              {filtersActive && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setFilters(EMPTY_AGENT_FILTERS)}
                >
                  <X className="size-3.5" />
                  Clear filters
                </Button>
              )}
            </div>

            {filtered.length === 0 ? (
              <p className="text-dense text-text-secondary">
                No agents match the current filters.
              </p>
            ) : (
              <AgentGrid agents={filtered} summaryByAgentId={summaryByAgentId} />
            )}
            {scoresLoading && (
              <p className="text-caption text-text-tertiary">Updating scores…</p>
            )}
          </>
        )}
      </PageBody>
    </Page>
  );
}

/** Four across on wide screens (the shared card rhythm with the catalog + runs), stepping down
 *  responsively. Cards STRETCH to the tallest in their row (equal-height), with the action row
 *  pinned to the bottom. */
function AgentGrid({
  agents,
  summaryByAgentId,
}: {
  agents: AgentEntry[];
  summaryByAgentId: Map<string, AgentPerformanceSummary>;
}) {
  return (
    <ul className="grid items-stretch gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {agents.map((agent, i) => (
        <li key={agent.id}>
          <Reveal delay={i * 40} className="h-full">
            <AgentCard agent={agent} summary={summaryByAgentId.get(agent.id)} />
          </Reveal>
        </li>
      ))}
    </ul>
  );
}

function AgentCard({
  agent,
  summary,
}: {
  agent: AgentEntry;
  summary: AgentPerformanceSummary | undefined;
}) {
  const { Logo, name: harnessName } = harnessOf(agent.kind);
  const detailHref = `/agents/detail?name=${encodeURIComponent(agent.name)}`;
  // One create-run experience: hand off to /runs/new with the agent preselected
  // rather than running a second, cut-down flow in a modal.
  const runHref = `/runs/new?agent=${encodeURIComponent(agent.name)}`;

  return (
    // h-full equalises cards WITHIN a row; min-h gives every card a consistent floor so a card
    // alone on the last row isn't slimmer than the rest. The action row is bottom-anchored so the
    // Start/View buttons line up whether or not the agent has evaluation stats yet.
    <Card className="flex h-full min-h-[13.5rem] flex-col p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-primary">
          <Logo />
          <span className="truncate text-row font-semibold text-heading">
            {agent.name}
          </span>
        </div>
        {agent.otel && (
          <Badge variant="ok" title="Telemetry endpoint configured">
            Telemetry
          </Badge>
        )}
      </div>

      <p className="mt-2 truncate text-dense text-text-secondary">
        {harnessName}
        {agent.model && (
          <>
            <span className="text-text-tertiary" aria-hidden>
              {" · "}
            </span>
            {agent.model}
          </>
        )}
      </p>

      {summary ? (
        <PerformanceSummary summary={summary} columns={3} className="mt-3" />
      ) : (
        // Centre the empty-state line in the card's body rather than stranding it at the top with
        // dead space below — flex-1 claims the space between the header and the action row.
        <p className="flex flex-1 items-center justify-center px-2 text-center text-dense text-text-tertiary">
          No evaluations yet — start a run to generate performance data.
        </p>
      )}

      <div className="mt-auto flex items-center gap-2 pt-4">
        <Link href={runHref} className={cn(buttonVariants({ size: "sm" }))}>
          <Play className="size-3.5" />
          Start Run
        </Link>
        <Link
          href={detailHref}
          className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
        >
          View Agent
        </Link>
      </div>
    </Card>
  );
}

/** No agents registered — what / why / next (report §17). */
function EmptyAgents({ onRegister }: { onRegister: () => void }) {
  return (
    <Card className="flex flex-col items-center gap-3 p-6 text-center">
      <CustomMark />
      <div className="space-y-2">
        <p className="text-body font-semibold text-heading">No agents yet</p>
        <p className="mx-auto max-w-sm text-body text-text-secondary">
          Register an agent to describe the AI harness you want to evaluate, then
          start a run to generate performance data.
        </p>
      </div>
      <Button className="mt-1" onClick={onRegister}>
        <Plus className="size-4" />
        Register agent
      </Button>
    </Card>
  );
}

/** Stable-sized skeleton cards while the agents list loads (report §17). The tile grid reuses
 *  the PerformanceSummary skeleton so the placeholder matches a loaded card. */
function LoadingCards() {
  return (
    <ul
      role="status"
      aria-label="Loading agents…"
      className="grid items-stretch gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
    >
      {[0, 1, 2].map((i) => (
        <li key={i}>
          <Card className="flex h-full min-h-[13.5rem] flex-col p-3">
            <div className="flex items-center gap-2">
              <Skeleton variant="icon" className="rounded" />
              <Skeleton variant="title" className="w-24" />
            </div>
            <Skeleton variant="text" className="mt-2" />
            <PerformanceSummary loading columns={3} className="mt-3" />
            <div className="mt-auto flex gap-2 pt-4">
              <Skeleton variant="button" />
              <Skeleton variant="button" className="w-24" />
            </div>
          </Card>
        </li>
      ))}
    </ul>
  );
}
