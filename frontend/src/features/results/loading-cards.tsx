import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PerformanceSummary } from "./performance-summary";

/** Card-grid skeleton shared by the results tabs (per-run and per-mission). */
export function LoadingCards({
  count = 2,
  label = "Loading results…",
}: {
  count?: number;
  label?: string;
}) {
  return (
    <div
      role="status"
      aria-label={label}
      className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
    >
      {Array.from({ length: count }).map((_, i) => (
        <Card key={i} className="p-4">
          <Skeleton variant="title" className="w-32" />
          <PerformanceSummary loading columns={3} className="mt-4" />
        </Card>
      ))}
    </div>
  );
}
