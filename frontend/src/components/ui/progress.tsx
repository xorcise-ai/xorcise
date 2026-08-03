import type { HTMLAttributes } from "react";
import { cn } from "./cn";

export interface ProgressProps extends HTMLAttributes<HTMLDivElement> {
  /** Progress in the range 0..100. */
  value?: number;
  /** Unknown-duration work: a sliding bar instead of a measured fill. */
  indeterminate?: boolean;
}

export function Progress({
  className,
  value = 0,
  indeterminate = false,
  ...props
}: ProgressProps) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(clamped)}
      className={cn(
        "h-1.5 w-full overflow-hidden rounded-full bg-muted",
        className,
      )}
      {...props}
    >
      {indeterminate ? (
        <div className="progress-indeterminate h-full w-1/3 rounded-full bg-primary" />
      ) : (
        <div
          className="h-full rounded-full bg-primary transition-all"
          style={{ width: `${clamped}%` }}
        />
      )}
    </div>
  );
}
