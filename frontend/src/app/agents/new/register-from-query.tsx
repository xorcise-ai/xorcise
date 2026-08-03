"use client";

import { useSearchParams } from "next/navigation";
import { RegisterAgentPage } from "@/features/agents/register-agent-page";

/** `?agent=<name>` switches the page to edit mode (query-param pattern — static-export safe;
 *  the read lives in this one client island so the route shell stays a server component). */
export function RegisterFromQuery() {
  const params = useSearchParams();
  return <RegisterAgentPage editName={params.get("agent")} />;
}
