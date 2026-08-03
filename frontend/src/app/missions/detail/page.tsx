import type { Metadata } from "next";
import { Suspense } from "react";
import { MissionDetailFromQuery } from "./mission-detail-from-query";

export const metadata: Metadata = { title: "Mission" };

export default function MissionDetailPage() {
  return (
    <Suspense>
      <MissionDetailFromQuery />
    </Suspense>
  );
}
