"use client";

import { Page, PageBody, PageHead, PageTitle } from "@/components/layout/page";
import { Overview } from "./overview";
import { RecentRuns } from "./recent-runs";
import { FleetMetrics } from "./fleet-metrics";
import { TopAgents, TopMissions } from "./leaderboards";

/**
 * The operational home, shown at / once an operator is set up and has runs (see the Home router).
 * Reads top-down as an operational overview: counts + system health, then fleet-wide performance,
 * then — filling the wide lower pane sideways instead of leaving it dead — a side rail of
 * leaderboards (best agents, most-run missions) beside the recent-runs log. A score-distribution
 * bar chart used to sit between the leaderboards; it was dropped because five 20-point buckets over
 * every graded run told an operator nothing they act on, which the leaderboards and the fleet
 * metrics above already cover.
 *
 * Frame: at lg+ this is the fixed operational frame — the counts + fleet metrics are shrink-0
 * chrome and the residual region is a row (a 22rem rail of leaderboards whose lists become
 * bounded internal scrollers, beside RecentRuns filling the rest). Nothing but the inner lists
 * scrolls.
 *
 * Below lg it is an ordinary SCROLLING document instead. The rail's cards are ~500px of shrink-0
 * content, which is more than a phone's whole body region: inside a non-scrolling frame the only
 * flexible sibling absorbed the entire squeeze, so RecentRuns collapsed to zero height and
 * vanished under its own heading. `min-h-0` is therefore lg-only — below it, min-height:auto keeps
 * every card at its natural size and the page scrolls, which is what a narrow viewport wants
 * anyway.
 */
export function DashboardView() {
  return (
    <Page className="gap-3">
      <PageHead>
        <PageTitle subtitle="Counts, system health, and how your fleet is performing.">
          Dashboard
        </PageTitle>
      </PageHead>

      <PageBody
        scroll={false}
        className="gap-3 overflow-y-auto pr-1 lg:overflow-hidden lg:pr-0"
      >
        <div className="shrink-0">
          <Overview />
        </div>
        <FleetMetrics />
        <div className="flex flex-col gap-3 lg:min-h-0 lg:flex-1 lg:flex-row">
          <aside className="flex shrink-0 flex-col gap-3 lg:min-h-0 lg:w-[22rem]">
            {/* The two leaderboards own the rail between them: at lg they split its residual
                height and scroll internally (each Card is already a min-h-0 flex column over an
                overflow-y-auto list), so removing the score-distribution chart leaves no dead
                space where it used to sit. Below lg they keep the max-h cap and the page scrolls. */}
            <TopAgents className="max-h-60 shrink-0 lg:max-h-none lg:min-h-0 lg:flex-1" />
            <TopMissions className="max-h-60 shrink-0 lg:max-h-none lg:min-h-0 lg:flex-1" />
          </aside>
          <div className="flex flex-col lg:min-h-0 lg:flex-1">
            <RecentRuns />
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
