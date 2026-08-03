"use client";

import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Page, PageBody, PageHead, PageTitle } from "@/components/layout/page";
import { CompletedRuns } from "./completed-runs";
import { ResultsByMission } from "./results-missions";
import { ResultsAllRuns } from "./results-all-runs";

/**
 * Main Results page shell (report §14). Owns the page header and a segmented control over the
 * three views — Agents (the default agent-centric performance cards from task 4.1), Missions
 * (the same data grouped by mission) and All Runs (a flat list of every run). Each view is a
 * self-contained, client-side re-presentation of data the Results feature already fetches; no
 * backend change and no capability removed (per-run scorecards stay reachable from All Runs and
 * from each agent's profile).
 */
export function ResultsTabs() {
  return (
    <Page className="gap-3">
      <PageHead>
        <PageTitle
          eyebrow="Results"
          subtitle="How agents and missions are performing across completed evaluations."
        >
          Performance
        </PageTitle>
      </PageHead>

      {/* The tab strip is chrome and stays put; the active panel is the one
          unbounded region, so switching views never moves the control. */}
      <PageBody scroll={false}>
        <Tabs defaultValue="agents" className="min-h-0 flex-1">
          <TabsList className="shrink-0">
            <TabsTrigger value="agents">Agents</TabsTrigger>
            <TabsTrigger value="missions">Missions</TabsTrigger>
            <TabsTrigger value="all-runs">All Runs</TabsTrigger>
          </TabsList>

          <TabsContent
            value="agents"
            className="min-h-0 flex-1 overflow-y-auto pr-1"
          >
            <CompletedRuns />
          </TabsContent>
          <TabsContent
            value="missions"
            className="min-h-0 flex-1 overflow-y-auto pr-1"
          >
            <ResultsByMission />
          </TabsContent>
          <TabsContent
            value="all-runs"
            className="min-h-0 flex-1 overflow-y-auto pr-1"
          >
            <ResultsAllRuns />
          </TabsContent>
        </Tabs>
      </PageBody>
    </Page>
  );
}
