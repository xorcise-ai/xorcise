import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "./cn";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-label uppercase",
  {
    variants: {
      variant: {
        default: "border-primary/20 bg-primary/10 text-primary",
        ok: "border-ok/20 bg-ok/10 text-ok",
        err: "border-err/20 bg-err/10 text-err",
        warn: "border-warning/20 bg-warning/10 text-warning",
        info: "border-info/20 bg-info/10 text-info",
        muted:
          "border-border bg-[rgba(255,255,255,0.05)] text-text-secondary",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

/* Status glyph so state is carried by glyph + brightness, never hue alone
   (guide §12). Only the status variants get a glyph; default/muted stay plain. */
const statusGlyph: Partial<Record<NonNullable<BadgeProps["variant"]>, string>> = {
  ok: "⊕",
  err: "✕",
  warn: "◔",
};

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, children, ...props }: BadgeProps) {
  const glyph = variant ? statusGlyph[variant] : undefined;
  return (
    <span className={cn(badgeVariants({ variant }), className)} {...props}>
      {glyph && <span aria-hidden>{glyph}</span>}
      {children}
    </span>
  );
}
