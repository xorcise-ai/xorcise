"use client";

import { useSearchParams } from "next/navigation";
import { MissionDetail } from "@/features/missions/mission-detail";

/** The URL read lives here, one client island, so the route shell above can stay
 *  a server component and export its own <title>. */
export function MissionDetailFromQuery() {
  const id = useSearchParams().get("id");
  return <MissionDetail id={id} />;
}
