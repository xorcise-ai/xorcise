import type { Metadata } from "next";
import { Suspense } from "react";
import { AgentDetailFromQuery } from "./agent-detail-from-query";

export const metadata: Metadata = { title: "Agent" };

export default function AgentDetailPage() {
  return (
    <Suspense>
      <AgentDetailFromQuery />
    </Suspense>
  );
}
