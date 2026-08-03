"use client";

import { useSearchParams } from "next/navigation";
import { NewRunForm } from "@/features/runs/new-run-form";

/** Preselection is carried in the URL so every surface (agent card, agent detail,
 *  mission detail) can deep-link into the one create-run flow. The read lives
 *  here, one client island, so the route shell can stay a server component and
 *  export its own <title>. */
export function NewRunFromQuery() {
  const params = useSearchParams();
  return (
    <NewRunForm
      initialAgent={params.get("agent") ?? ""}
      initialMission={params.get("mission") ?? ""}
    />
  );
}
