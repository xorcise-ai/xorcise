import type { Metadata } from "next";
import { Suspense } from "react";
import { RunLiveFromQuery } from "./run-live-from-query";

export const metadata: Metadata = { title: "Live run" };

export default function RunLivePage() {
  return (
    <Suspense>
      <RunLiveFromQuery />
    </Suspense>
  );
}
