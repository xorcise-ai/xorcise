import { Loader2 } from "lucide-react";
import { cn } from "./cn";

export function Loading({
  className,
  label = "Loading…",
}: {
  className?: string;
  label?: string;
}) {
  return (
    <div
      role="status"
      className={cn(
        "flex items-center gap-2 text-dense text-text-secondary",
        className,
      )}
    >
      <Loader2 className="size-4 motion-safe:animate-spin text-primary" />
      <span>{label}</span>
    </div>
  );
}
