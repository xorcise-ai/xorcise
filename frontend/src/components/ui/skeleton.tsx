import type { HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "./cn";

const skeletonVariants = cva("skeleton-shimmer rounded", {
  variants: {
    variant: {
      block: "",
      text: "h-3 w-32",
      title: "h-4 w-40",
      stat: "h-6 w-10",
      icon: "size-5",
      button: "h-7 w-20",
      row: "h-6 w-full",
    },
  },
  defaultVariants: { variant: "block" },
});

export type SkeletonProps = HTMLAttributes<HTMLDivElement> &
  VariantProps<typeof skeletonVariants>;

export function Skeleton({ className, variant, ...props }: SkeletonProps) {
  return (
    <div className={cn(skeletonVariants({ variant }), className)} {...props} />
  );
}

/** A stack of full-width row placeholders — the generic list/table skeleton. */
export function SkeletonRows({
  count = 4,
  className,
}: {
  count?: number;
  className?: string;
}) {
  return (
    <div className={cn("space-y-3", className)}>
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} variant="row" />
      ))}
    </div>
  );
}

/** A responsive grid of card-shaped placeholders (title + text rows). */
export function SkeletonCardGrid({
  count = 3,
  className,
}: {
  count?: number;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4",
        className,
      )}
    >
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-xl border border-border bg-card p-4">
          <Skeleton variant="title" />
          <Skeleton variant="text" className="mt-3 w-full" />
          <Skeleton variant="text" className="mt-2 w-2/3" />
        </div>
      ))}
    </div>
  );
}
