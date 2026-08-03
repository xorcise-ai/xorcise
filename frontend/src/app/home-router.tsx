"use client";

import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { Welcome } from "@/features/setup/welcome";
import { DashboardView } from "@/features/dashboard/dashboard-view";
import { useReadiness } from "@/features/setup/readiness";
import { useRuns } from "@/features/runs/queries";

/**
 * First-run router for `/`. A fresh install (no runs yet) or an incomplete setup
 * lands on the Welcome landing; once the operator has runs AND setup is ready, `/`
 * is the operational Dashboard. A neutral loading state avoids flashing the wrong
 * surface before the run/readiness queries resolve.
 *
 * The decision needs the query hooks, so it is a client island; the route shell
 * above stays a server component and owns the `/` <title>.
 */
export function HomeRouter() {
  const readiness = useReadiness();
  const runs = useRuns();
  const runsLoading = !runs.data && !runs.isError;

  if (readiness.loading || runsLoading) {
    return (
      <div role="status" aria-label="Loading…" className="space-y-4 p-6">
        <Skeleton variant="title" className="w-64" />
        <SkeletonRows count={6} />
      </div>
    );
  }

  const hasRuns = (runs.data?.length ?? 0) > 0;
  return hasRuns && readiness.ready ? <DashboardView /> : <Welcome />;
}
