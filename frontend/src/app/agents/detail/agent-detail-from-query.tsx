"use client";

import { useSearchParams } from "next/navigation";
import { AgentDetail } from "@/features/agents/agent-detail";

/** The URL read lives here, one client island, so the route shell above can stay
 *  a server component and export its own <title>. */
export function AgentDetailFromQuery() {
  const name = useSearchParams().get("name");
  return <AgentDetail name={name} />;
}
