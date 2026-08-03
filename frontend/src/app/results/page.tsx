import type { Metadata } from "next";
import { ResultsTabs } from "@/features/results/results-tabs";

// The page's own heading, not the nav label ("Results") — the tab reads the same
// word the operator sees at the top of the surface.
export const metadata: Metadata = { title: "Performance" };

export default function ResultsPage() {
  return <ResultsTabs />;
}
