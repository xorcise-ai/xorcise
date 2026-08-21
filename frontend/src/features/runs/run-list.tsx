"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Plus } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Page, PageBody, PageHead, PageTitle } from "@/components/layout/page";
import { Reveal } from "@/components/ui/reveal";
import { SkeletonCardGrid } from "@/components/ui/skeleton";
import { useAgents } from "@/features/agents/queries";
import { useRuns } from "./queries";
import { RunCard } from "./run-card";
import {
  EMPTY_RUN_FILTERS,
  RunFilterBar,
  distinctAgents,
  distinctMissions,
  distinctStatuses,
  filterRuns,
  hasActiveRunFilters,
  type RunFilters,
} from "./run-filters";

export function RunList() {
  const runs = useRuns();
  const agents = useAgents();
  const [filters, setFilters] = useState<RunFilters>(EMPTY_RUN_FILTERS);

  // Resolve agent names once (RunCard does the same per-card) so search + the Agent facet key on
  // what the operator sees, not opaque ids.
  const agentNameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of agents.data ?? []) m.set(a.id, a.name);
    return m;
  }, [agents.data]);

  const allRuns = runs.data ?? [];
  const filtered = useMemo(
    () => filterRuns(allRuns, agentNameById, filters),
    [allRuns, agentNameById, filters],
  );
  const statuses = useMemo(() => distinctStatuses(allRuns), [allRuns]);
  const agentOptions = useMemo(
    () => distinctAgents(allRuns, agentNameById),
    [allRuns, agentNameById],
  );
  const missionOptions = useMemo(
    () => distinctMissions(allRuns),
    [allRuns],
  );

  const hasRuns = allRuns.length > 0;
  const active = hasActiveRunFilters(filters);

  return (
    // Three bands: the head + the filter bar are chrome and stay put; the card
    // grid is the one unbounded region and scrolls inside itself, so the filters
    // never leave with the results they control.
    <Page className="gap-3">
      <PageHead>
        <PageTitle eyebrow="Runs">Run history</PageTitle>
        <Link href="/runs/new" className={buttonVariants()}>
          <Plus className="size-4" />
          New run
        </Link>
      </PageHead>

      {hasRuns && (
        <div className="shrink-0">
          <RunFilterBar
            filters={filters}
            onChange={setFilters}
            statuses={statuses}
            agents={agentOptions}
            missions={missionOptions}
          />
        </div>
      )}

      <PageBody>
        {runs.isLoading && (
          <div role="status" aria-label="Loading runs…">
            <SkeletonCardGrid count={4} />
          </div>
        )}
        {runs.isError && <p className="text-body text-err">Couldn’t load runs.</p>}

        {runs.data?.length === 0 && (
          <Card className="p-6 text-center">
            <p className="text-body text-text-secondary">
              No runs yet. Start one to evaluate an agent against a mission.
            </p>
            <Link
              href="/runs/new"
              className={`${buttonVariants({ variant: "outline", size: "sm" })} mt-3`}
            >
              <Plus className="size-3.5" />
              New run
            </Link>
          </Card>
        )}

        {hasRuns && filtered.length > 0 && (
          // Three across at the widest, not four: a run card carries more facts than a catalog
          // card (name, mission, agent, harness, time, elapsed/budget) and at four columns
          // they wrapped into a cramped block. The extra width also gives the in-card delete
          // confirmation room to read as a prompt rather than a squeeze.
          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((run, i) => (
              <li key={run.run_id}>
                <Reveal delay={i * 40} className="h-full">
                  <RunCard run={run} />
                </Reveal>
              </li>
            ))}
          </ul>
        )}

        {hasRuns && filtered.length === 0 && (
          <Card className="p-6 text-center">
            <p className="text-body text-text-secondary">
              No runs match these filters.
            </p>
            {active && (
              <Button
                variant="ghost"
                size="sm"
                className="mt-3"
                onClick={() => setFilters(EMPTY_RUN_FILTERS)}
              >
                Clear filters
              </Button>
            )}
          </Card>
        )}
      </PageBody>
    </Page>
  );
}
