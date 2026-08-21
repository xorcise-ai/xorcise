import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "./cn";

/* The status dot. Six pixels, one job: colour a state that the text beside it already
   names. It is deliberately NOT a standalone status indicator — the guide's §12 rule is
   that state is carried by glyph + brightness and never by hue alone, which is satisfied
   here only because a dot never appears without its label (Chip, the status bar, chart
   legends). A dot on its own is a design-system violation; use a Badge, which carries a
   glyph. */
const dotVariants = cva("shrink-0 rounded-full", {
  variants: {
    tone: {
      ok: "bg-ok",
      err: "bg-err",
      warn: "bg-warning",
      info: "bg-info",
      model: "bg-model",
      primary: "bg-primary",
      muted: "bg-muted-foreground",
    },
    size: {
      /* The Figma chip dot. */
      default: "size-1.5",
      /* Legend swatches, which sit beside 11px caption text and need the extra pixel. */
      lg: "size-2",
    },
  },
  defaultVariants: { tone: "muted", size: "default" },
});

export interface StatusDotProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof dotVariants> {}

export function StatusDot({ className, tone, size, ...props }: StatusDotProps) {
  return (
    <span
      aria-hidden
      className={cn(dotVariants({ tone, size }), className)}
      {...props}
    />
  );
}

export type DotTone = NonNullable<StatusDotProps["tone"]>;
export { dotVariants };
