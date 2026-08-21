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
        // heading/5, not white/5: Figma washes the muted chip with rgba(240,240,240,.05),
        // i.e. the HEADING value at 5% rather than pure white. On this ground the two are a
        // single step apart, but naming it after a palette entry is what stops the value
        // drifting the next time someone eyeballs it.
        muted: "border-border bg-heading/5 text-text-secondary",
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
