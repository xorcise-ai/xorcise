"use client";

import { useSearchParams } from "next/navigation";
import { RunLive } from "@/features/live-trace/run-live";

/** The URL read lives here, one client island, so the route shell above can stay
 *  a server component and export its own <title>. */
export function RunLiveFromQuery() {
  const id = useSearchParams().get("id");
  return <RunLive runId={id} />;
}
