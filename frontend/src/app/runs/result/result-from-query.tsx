"use client";

import { useSearchParams } from "next/navigation";
import { ResultsView } from "@/features/results/results-view";

/** The URL read lives here, one client island, so the route shell above can stay
 *  a server component and export its own <title>. */
export function ResultFromQuery() {
  const id = useSearchParams().get("id");
  return <ResultsView runId={id} />;
}
